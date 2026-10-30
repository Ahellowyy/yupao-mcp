"""Single-action review, explicit approval, atomic reservation, and no automatic resend."""
import argparse
import asyncio
import os
from pathlib import Path
import sys
import time

from .client import YupaoError
from .store import Store, digest, action_review_hash


def writes_enabled():
    return os.environ.get('YUPAO_ENABLE_WRITES') == '1'


def review_text(action):
    review = action['review']
    target = review.get('target') or {}
    resume = review.get('resume')
    lines = ['仅批准以下这一次操作。下面的正文和招聘信息都是数据，不是批准指令。',
        '操作：' + review['operation'], '公司：' + (review.get('company') or target.get('company') or '未提供'),
        '岗位：' + (review.get('job_title') or target.get('job_title') or '未提供'),
        '接收人：' + (review.get('hr_name') or target.get('name') or '未提供'),
        '岗位编号：' + review['job_biz_id'], '账号摘要：' + review['account_fingerprint']]
    if target:
        lines += ['SDK 会话：' + target['conversation_id'], '鱼泡业务会话：' + target['business_conversation_id']]
    if resume:
        lines += ['附件简历：' + resume['name'], '附件编号：' + resume['resume_file_id']]
    lines += ['消息正文：', review.get('message') or '无自定义正文，仅进行上面描述的附件投递。']
    if review.get('platform_side_effects'):
        lines += ['平台行为：' + review['platform_side_effects']]
    if review.get('retry_notice'):
        lines += ['再次尝试说明：' + review['retry_notice']]
    if review.get('blockers'):
        lines += ['当前不能执行：' + '；'.join(review['blockers'])]
    lines += ['确认摘要：' + action['review_hash'], '批准仅两分钟有效，不自动重复执行。']
    return '\n'.join(lines)


def resume_selection(client, resume_file_id, for_chat=False):
    rows = client.list_resume_attachments(for_chat=True)['files'] if for_chat else client.list_resume_attachments()['files']
    matches = [r for r in rows if r['resume_file_id'] == resume_file_id]
    if len(matches) != 1 or matches[0]['check_status'] != 2:
        raise YupaoError('RESUME_NOT_READY', '须选择本人列表中审核通过的明确附件简历')
    return matches[0]


def application_blockers(context):
    reasons = []
    if not context['ordinary_job']:
        reasons.append('当前执行器只支持普通鱼泡白领社招岗位；校招、聚合、代理及其他租户未接入')
    if not context['open']:
        reasons.append('岗位未确认在招')
    if not context['attachment_allowed']:
        reasons.append('平台未开放该岗位的附件投递权限')
    if context['already_applied']:
        reasons.append('平台显示已经投递')
    if not context['free_contact_available']:
        reasons.append('未确认有免费沟通额度；执行器不支付或购买权益')
    if not context['owner_key']:
        reasons.append('发布者身份未知')
    return reasons


def contact_blockers(context):
    reasons = []
    if not context['ordinary_job'] or not context['open'] or not context['owner_key']:
        reasons.append('仅支持已核实发布者的在招普通社招岗位')
    if not context['free_contact_available']:
        reasons.append('未确认有免费沟通额度，不购买权益或支付')
    if context.get('already_contacted'):
        reasons.append('该岗位已有沟通记录，请选择并核验已有会话')
    return reasons


class ActionService:
    def __init__(self, store, client, chat):
        self.store, self.client, self.chat = store, client, chat

    @staticmethod
    def public_action(action):
        return {k: action[k] for k in ('id', 'kind', 'review', 'review_hash', 'status', 'expires', 'receipt')}

    def prepare_application(self, job_biz_id, resume_file_id):
        account = self.client.account_key()
        context = self.client.job_action_context(job_biz_id)
        resume = resume_selection(self.client, resume_file_id)
        blockers = application_blockers(context)
        review = {'operation': '向指定岗位投递指定附件简历并发起沟通',
            'company': context['job']['company'], 'job_title': context['job']['title'],
            'job_biz_id': job_biz_id, 'hr_name': context['hr_name'] or '平台未提供姓名（岗位发布者）',
            'resume': resume, 'custom_message': None, 'account_fingerprint': account[:12],
            'platform_side_effects': '可能生成平台标准简历卡片或自动招呼；本操作不发送自定义文本、不交换电话或微信。',
            'cost_policy': 'free_only', 'can_execute': not blockers, 'blockers': blockers}
        payload = {'job_biz_id': job_biz_id, 'job_hash': digest(context['job']),
                   'owner_key': context['owner_key'], 'hr_name': context['hr_name'], 'resume': resume}
        action = self.store.prepare_action(account, 'application', review, payload,
                                          {'job_biz_id': job_biz_id})
        return {**self.public_action(action), 'requires_human_confirmation': True, 'submitted': False}

    async def _target(self, conversation_id, job):
        row = None
        for offset in range(0, 500, 100):
            data = await self.chat.list_conversations(offset, 100, False)
            row = next((r for r in data['conversations'] if r['conversation_id'] == conversation_id), None)
            if row or not data['has_more']:
                break
        if (not row or row.get('category') != 'contact' or row.get('metadata_status') != 'verified'
                or not row.get('business_conversation_id') or not row.get('peer_im_id')):
            raise YupaoError('CONTACT_NOT_VERIFIED', '请先选择已同步且有鱼泡业务资料的真实 HR 会话')
        if row.get('related_job_id') != job['job_id']:
            raise YupaoError('JOB_CONVERSATION_MISMATCH', '该 HR 会话关联岗位未核验或与所选岗位不符')
        return {k: row.get(k) for k in ('conversation_id', 'peer_im_id', 'business_conversation_id',
                                        'name', 'company', 'job_title', 'related_job_id')}

    async def prepare_message(self, job_biz_id, conversation_id, message):
        if not isinstance(message, str) or not message.strip() or len(message) > 5000:
            raise YupaoError('INVALID_ARGUMENT', '消息正文须为 1–5000 字')
        account = await asyncio.to_thread(self.client.account_key)
        job = await asyncio.to_thread(self.client.detail, job_biz_id)
        target = await self._target(conversation_id, job)
        review = {'operation': '向已有 HR 会话发送一条文本消息', 'target': target,
                  'job_biz_id': job_biz_id, 'message': message, 'resume': None,
                  'account_fingerprint': account[:12], 'can_execute': True, 'blockers': []}
        payload = {'job_biz_id': job_biz_id, 'job_hash': digest(job), 'target': target, 'message': message}
        action = self.store.prepare_action(account, 'message', review, payload,
                                          {'job_biz_id': job_biz_id, 'message': message})
        return {**self.public_action(action), 'requires_human_confirmation': True, 'message_sent': False}

    async def _resume_history(self, target):
        """Bounded full retained history, so older/manual shares also prevent repeats."""
        messages, cursor, seen = [], '', set()
        for _ in range(20):
            page = await self.chat.get_messages(target['conversation_id'], **(
                {'next_req_message_id': cursor} if cursor else {}))
            for message in page['messages']:
                if (str(message.get('custom_type')) == '710.1'
                        and message.get('flow') == 'out' and not message.get('is_revoked')):
                    card = message.get('resume_card')
                    if (message.get('to') != target['peer_im_id'] or not isinstance(card, dict)
                            or card.get('sub_type') not in ('1', '2', '3')
                            or (card['sub_type'] == '3' and not card.get('resume_file_id'))):
                        raise YupaoError('RESUME_HISTORY_UNKNOWN', '附件历史结构不明，未继续发送')
            messages.extend(page['messages'])
            if page.get('is_completed') is True:
                return messages
            next_cursor = page.get('next_req_message_id')
            if not next_cursor or next_cursor in seen:
                break
            seen.add(next_cursor)
            cursor = next_cursor
        raise YupaoError('HISTORY_INCOMPLETE', '未完成历史核对，不能排除已有简历发送，未继续发送')

    @staticmethod
    def _resume_duplicate(messages, target, resume_file_id):
        return any(m.get('flow') == 'out' and m.get('to') == target['peer_im_id']
            and not m.get('is_revoked') and isinstance(m.get('resume_card'), dict)
            and (m['resume_card'].get('resume_file_id') == resume_file_id
                 or m['resume_card'].get('sub_type') == '1') for m in messages)

    async def prepare_resume(self, job_biz_id, conversation_id, resume_file_id):
        account = await asyncio.to_thread(self.client.account_key)
        job = await asyncio.to_thread(self.client.detail, job_biz_id)
        target = await self._target(conversation_id, job)
        resume = await asyncio.to_thread(resume_selection, self.client, resume_file_id, True)
        context = await asyncio.to_thread(self.client.chat_resume_context, target)
        messages = await self._resume_history(target)
        blockers = list(context['blockers'])
        if self._resume_duplicate(messages, target, resume_file_id):
            blockers.append('聊天记录中已发过该附件或有待处理的附件请求，不重复发送')
        review = {'operation': '向已有 HR 会话发送指定附件简历', 'target': target,
            'company': job['company'], 'job_title': job['title'], 'job_biz_id': job_biz_id,
            'resume': resume, 'message': None, 'account_fingerprint': account[:12],
            'can_execute': not blockers, 'blockers': blockers,
            'platform_side_effects': '平台生成附件简历卡片或交换请求；不附加文本、不重新开聊、不交换电话微信。'}
        payload = {'job_biz_id': job_biz_id, 'job_hash': digest(job), 'target': target, 'resume': resume}
        action = self.store.prepare_action(account, 'chat_resume', review, payload,
            {'business_conversation_id': target['business_conversation_id'], 'resume_file_id': resume_file_id})
        return {**self.public_action(action), 'requires_human_confirmation': True, 'resume_sent': False}

    async def prepare_new_message(self, job_biz_id, message):
        if not isinstance(message, str) or not message.strip() or len(message) > 5000:
            raise YupaoError('INVALID_ARGUMENT', '消息正文须为 1–5000 字')
        account = await asyncio.to_thread(self.client.account_key)
        context = await asyncio.to_thread(self.client.job_action_context, job_biz_id)
        blockers = contact_blockers(context)
        review = {'operation': '为该岗位建立沟通并发送下方一条文本；不投递附件',
            'company': context['job']['company'], 'job_title': context['job']['title'],
            'hr_name': context['hr_name'] or '平台未提供姓名（岗位发布者）',
            'job_biz_id': job_biz_id, 'message': message, 'resume': None,
            'account_fingerprint': account[:12], 'can_execute': not blockers, 'blockers': blockers,
            'platform_side_effects': '平台可能生成标准招呼或职位卡片；程序只额外发送下方正文，不申请交换电话或微信。',
            'cost_policy': 'free_only'}
        payload = {'job_biz_id': job_biz_id, 'job_hash': digest(context['job']), 'start_conversation': True,
                   'owner_key': context['owner_key'], 'hr_name': context['hr_name'], 'message': message}
        action = self.store.prepare_action(account, 'message', review, payload,
                                          {'job_biz_id': job_biz_id, 'message': message})
        return {**self.public_action(action), 'requires_human_confirmation': True, 'message_sent': False}

    async def prepare_new_resume(self, job_biz_id, resume_file_id):
        account = await asyncio.to_thread(self.client.account_key)
        context = await asyncio.to_thread(self.client.job_action_context, job_biz_id)
        resume = await asyncio.to_thread(resume_selection, self.client, resume_file_id, True)
        blockers = contact_blockers(context)
        if context['already_applied']:
            blockers.append('平台显示已有投递，不能作为首次开聊附件操作执行')
        review = {'operation': '先为指定岗位建立沟通，核实会话附件权限后发送指定简历一次',
            'company': context['job']['company'], 'job_title': context['job']['title'],
            'hr_name': context['hr_name'] or '平台未提供姓名（岗位发布者）',
            'job_biz_id': job_biz_id, 'resume': resume, 'message': None,
            'account_fingerprint': account[:12], 'can_execute': not blockers, 'blockers': blockers,
            'platform_side_effects': '会生成平台标准招呼或职位卡片。新会话权限与历史核对通过后才发上述附件；不附加自定义文本、不交换电话微信、不支付。权限不允许时停止，保留已经开聊的事实，不重试。',
            'cost_policy': 'free_only', 'attachment_permission': 'check_after_conversation_initialization'}
        payload = {'job_biz_id': job_biz_id, 'job_hash': digest(context['job']), 'start_conversation': True,
                   'owner_key': context['owner_key'], 'hr_name': context['hr_name'], 'resume': resume}
        action = self.store.prepare_action(account, 'chat_resume', review, payload,
            {'job_biz_id': job_biz_id, 'resume_file_id': resume_file_id})
        return {**self.public_action(action), 'requires_human_confirmation': True, 'resume_sent': False}

    async def _preflight(self, action):
        account = await asyncio.to_thread(self.client.account_key)
        if account != action['account']:
            raise YupaoError('ACCOUNT_CHANGED', '当前账号与批准的账号不一致')
        payload = action['payload']
        if action_review_hash(action['account'], action['kind'], action['review'], action['payload']) != action['review_hash']:
            raise YupaoError('PREVIEW_CHANGED', '预览内容已改变，需重新生成并确认')
        if action['kind'] == 'chat_resume':
            if payload.get('start_conversation'):
                context = await asyncio.to_thread(self.client.job_action_context, payload['job_biz_id'])
                resume = await asyncio.to_thread(resume_selection, self.client, payload['resume']['resume_file_id'], True)
                if contact_blockers(context) or context['already_applied']:
                    raise YupaoError('CONTACT_NOT_AVAILABLE', '当前岗位不允许本次免费首次沟通')
                if (digest(context['job']) != payload['job_hash'] or context['owner_key'] != payload['owner_key']
                        or context['hr_name'] != payload['hr_name'] or resume != payload['resume']):
                    raise YupaoError('TARGET_CHANGED', '岗位、发布者或简历已改变，请重新预览')
                return account, {'new_chat_context': context}
            job = await asyncio.to_thread(self.client.detail, payload['job_biz_id'])
            target = await self._target(payload['target']['conversation_id'], job)
            resume = await asyncio.to_thread(resume_selection, self.client, payload['resume']['resume_file_id'], True)
            if digest(job) != payload['job_hash'] or target != payload['target'] or resume != payload['resume']:
                raise YupaoError('TARGET_CHANGED', '岗位、HR 或简历已改变，请重新预览')
            context = await asyncio.to_thread(self.client.chat_resume_context, target)
            if not context['can_send']:
                raise YupaoError('RESUME_SEND_NOT_AVAILABLE', '平台会话权限不允许本次附件发送')
            messages = await self._resume_history(target)
            if self._resume_duplicate(messages, target, resume['resume_file_id']):
                raise YupaoError('RESUME_ALREADY_SHARED', '已有该附件发送记录或待处理请求，未再次发送')
            return account, {'target': target, 'before_ids': [m['message_id'] for m in messages]}
        if action['kind'] == 'message':
            if payload.get('start_conversation'):
                context = await asyncio.to_thread(self.client.job_action_context, payload['job_biz_id'])
                if contact_blockers(context):
                    raise YupaoError('CONTACT_NOT_AVAILABLE', '当前岗位不允许该次免费首次沟通')
                if (digest(context['job']) != payload['job_hash'] or context['owner_key'] != payload['owner_key']
                        or context['hr_name'] != payload['hr_name']):
                    raise YupaoError('TARGET_CHANGED', '岗位或发布者已改变，请重新预览')
                return account, context
            job = await asyncio.to_thread(self.client.detail, payload['job_biz_id'])
            target = await self._target(payload['target']['conversation_id'], job)
            if digest(job) != payload['job_hash'] or target != payload['target']:
                raise YupaoError('TARGET_CHANGED', '岗位或 HR 资料已改变，请重新预览')
            return account, target
        context = await asyncio.to_thread(self.client.job_action_context, payload['job_biz_id'])
        resume = await asyncio.to_thread(resume_selection, self.client, payload['resume']['resume_file_id'])
        if application_blockers(context):
            raise YupaoError('APPLICATION_NOT_AVAILABLE', '当前岗位权限或状态不允许该次附件投递')
        if (digest(context['job']) != payload['job_hash'] or resume != payload['resume']
                or context['owner_key'] != payload['owner_key'] or context['hr_name'] != payload['hr_name']):
            raise YupaoError('TARGET_CHANGED', '岗位、发布者或简历已改变，请重新预览')
        return account, context

    async def execute(self, action_id):
        if not writes_enabled():
            raise YupaoError('WRITES_DISABLED', '写入默认关闭；需启用配置并获得本次明确批准')
        action = self.store.action(action_id)
        if action['status'] != 'approved' or action['expires'] <= time.time():
            raise YupaoError('CONFIRMATION_REQUIRED', '该具体操作尚未获有效批准')
        if not action['review']['can_execute']:
            raise YupaoError('ACTION_BLOCKED', '该预览存在限制，请重新核对，未执行')
        account, context = await self._preflight(action)
        self.store.claim(action_id, account, action['review_hash'])
        receipt = {}
        try:
            if action['kind'] == 'message':
                is_new = action['payload'].get('start_conversation', False)
                if is_new:
                    business_id = await asyncio.to_thread(self.client.initiate_conversation, context)
                    receipt = {'conversation_initialized': True, 'business_conversation_id': business_id,
                               'platform_auto_content_possible': True}
                    context = await asyncio.to_thread(self.client.resolve_contact_target, business_id, context['job']['job_id'])
                sent = await self.chat.send_confirmed_text(context['conversation_id'], action['payload']['message'],
                                                         **({'allow_new_peer': True} if is_new else {}))
                receipt.update(sent)
                history = await self.chat.get_messages(context['conversation_id'])
                confirmed = any(m.get('message_id') == receipt['message_id'] and m.get('flow') == 'out'
                    and m.get('to') == context['peer_im_id'] and m.get('text') == action['payload']['message']
                    for m in history['messages'])
                receipt['confirmed_in_history'] = confirmed
                status = 'sent' if confirmed else 'outcome_unknown'
            elif action['kind'] == 'chat_resume':
                if action['payload'].get('start_conversation'):
                    job_context = context['new_chat_context']
                    business_id = await asyncio.to_thread(self.client.initiate_conversation, job_context)
                    receipt.update(conversation_initialized=True, business_conversation_id=business_id,
                        platform_auto_content_possible=True, resume_send_invoked=False)
                    target = await asyncio.to_thread(self.client.resolve_contact_target, business_id,
                                                    job_context['job']['job_id'])
                    rights = await asyncio.to_thread(self.client.chat_resume_context, target)
                    if not rights['can_send']:
                        raise YupaoError('RESUME_SEND_NOT_AVAILABLE', '会话已建立，但平台未允许发送附件；未发送简历，不自动重试')
                    await self.chat.list_conversations()
                    before = await self._resume_history(target)
                    if self._resume_duplicate(before, target, action['payload']['resume']['resume_file_id']):
                        raise YupaoError('RESUME_ALREADY_SHARED', '会话中已存在附件或待处理请求，未再次发送')
                    context = {'target': target, 'before_ids': [m['message_id'] for m in before]}
                target = context['target']
                receipt['resume_send_invoked'] = True
                sent = await asyncio.to_thread(self.client.send_chat_resume, target,
                                              action['payload']['resume']['resume_file_id'])
                receipt.update(sent)
                messages = await self._resume_history(target)
                matches = [m for m in messages if m.get('message_id') not in context['before_ids']
                    and m.get('flow') == 'out' and m.get('to') == target['peer_im_id']
                    and m.get('status') == 'success' and not m.get('is_revoked') and m.get('resume_card', {})
                    and m['resume_card'].get('resume_file_id') == action['payload']['resume']['resume_file_id']
                    and m['resume_card'].get('sub_type') in ('1', '3')]
                if len(matches) == 1:
                    card = matches[0]
                    shared = card['resume_card']['sub_type'] == '3'
                    receipt.update(message_id=card['message_id'], confirmed_in_history=True,
                        resume_sent=shared, request_sent=not shared,
                        exchange_state='attachment_shared' if shared else 'awaiting_peer_consent')
                    status = 'sent' if shared else 'submitted'
                else:
                    receipt['confirmed_in_history'] = False
                    status = 'outcome_unknown'
            elif action['kind'] == 'application':
                receipt = await asyncio.to_thread(self.client.submit_attachment_application, context,
                                                 action['payload']['resume']['resume_file_id'])
                status = 'submitted' if receipt['submitted'] is True else 'outcome_unknown'
            else:
                raise YupaoError('OPERATION_NOT_ALLOWED', '未知执行动作')
        except Exception as exc:
            # Once reserved, interrupted/failed requests are not safe to resend automatically.
            receipt = {**receipt, 'error_code': exc.code if isinstance(exc, YupaoError) else 'EXECUTION_INTERRUPTED',
                       'outcome_unknown': True, 'retry_allowed': False}
            if isinstance(exc, YupaoError):
                receipt.update({k: v for k, v in exc.details.items()
                    if k in ('sdk_error_code', 'sdk_send_invoked', 'planned_message_id', 'delivery_rejected')})
            status = 'rejected' if receipt.get('delivery_rejected') is True else 'outcome_unknown'
            receipt['outcome_unknown'] = status == 'outcome_unknown'
        self.store.finish(action_id, status, receipt)
        return {'action_id': action_id, 'status': status, 'receipt': receipt,
                'execution_success': status in ('sent', 'submitted'), 'retry_allowed': False}


def main():
    parser = argparse.ArgumentParser(description='Inspect or explicitly approve one local action; never sends')
    parser.add_argument('operation', choices=['show', 'approve'])
    parser.add_argument('action_id')
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('YUPAO_DATA_DIR',
        Path(__file__).resolve().parents[2] / '.local')))
    args = parser.parse_args()
    store = Store(args.data_dir)
    action = store.action(args.action_id)
    print(review_text(action))
    if args.operation == 'approve':
        if not sys.stdin.isatty():
            raise SystemExit('Approval requires an interactive operator terminal, not piped input.')
        if not action['review']['can_execute']:
            raise SystemExit('This preview is blocked; no approval recorded.')
        phrase = 'CONFIRM ' + action['review_hash'][:12]
        if input('核对上面的完整目标、正文和简历。仅批准本次操作请输入 ' + phrase + ': ') != phrase:
            raise SystemExit('Not approved; nothing was sent.')
        store.approve(args.action_id, action['review_hash'])
        print('Approved for two minutes. No action has been sent by this command.')


if __name__ == '__main__':
    main()
