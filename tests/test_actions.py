import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest

from yupao_mcp.actions import ActionService
from yupao_mcp.client import YupaoError
from yupao_mcp.store import Store, encoded


class Client:
    def __init__(self):
        self.account = 'account-one'
        self.job = {'job_biz_id': 'Job123', 'job_id': '123', 'title': 'Agent', 'company': '示例公司', 'description': 'Python'}
        self.resume = {'resume_file_id': 'resume123', 'name': '示例简历', 'check_status': 2}
        self.available = True
        self.app_calls = 0
    def account_key(self): return self.account
    def detail(self, key): return copy.deepcopy(self.job)
    def list_resume_attachments(self): return {'files': [copy.deepcopy(self.resume)]}
    def job_action_context(self, key):
        return {'job': self.detail(key), 'hr_name': '示例 HR', 'owner_key': 'owner1', 'ordinary_job': True,
                'open': True, 'attachment_allowed': self.available, 'already_applied': False,
                'free_contact_available': True, 'already_contacted': False, 'occ_v2': []}
    def submit_attachment_application(self, context, resume_file_id):
        self.app_calls += 1
        return {'submitted': True, 'acknowledged': True, 'business_conversation_id': 'business123'}
    def initiate_conversation(self, context):
        self.app_calls += 1
        return 'business123'
    def resolve_contact_target(self, business_id, job_id):
        return {'conversation_id': 'C2Cperson', 'peer_im_id': 'person'}


class Chat:
    def __init__(self): self.sent = []; self.fail_history = False; self.target_job = '123'
    async def list_conversations(self, *args):
        return {'has_more': False, 'conversations': [{'conversation_id': 'C2Cperson', 'category': 'contact',
            'metadata_status': 'verified', 'peer_im_id': 'person', 'business_conversation_id': 'business123',
            'name': '示例 HR', 'company': '示例公司', 'job_title': 'Agent', 'related_job_id': self.target_job}]}
    async def send_confirmed_text(self, cid, message, **kwargs):
        self.sent.append(message)
        return {'message_id': 'message1', 'acknowledged': True, 'delivery_verified': False}
    async def get_messages(self, cid):
        if self.fail_history: raise YupaoError('NETWORK_ERROR', 'private detail')
        return {'messages': [{'message_id': 'message1', 'flow': 'out', 'to': 'person', 'text': self.sent[-1]}]}


@pytest.fixture
def service(tmp_path): return ActionService(Store(tmp_path), Client(), Chat())


def prepare(service): return asyncio.run(service.prepare_message('Job123', 'C2Cperson', '您好，项目介绍'))


def test_preparation_is_read_only_and_duplicate_drafts_reuse_same_id(service):
    first, second = prepare(service), prepare(service)
    assert first['id'] == second['id'] and service.chat.sent == []


def test_disabled_or_unapproved_actions_never_send(service, monkeypatch):
    action = prepare(service)
    monkeypatch.delenv('YUPAO_ENABLE_WRITES', raising=False)
    with pytest.raises(YupaoError) as error: asyncio.run(service.execute(action['id']))
    assert error.value.code == 'WRITES_DISABLED'
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    with pytest.raises(YupaoError) as error: asyncio.run(service.execute(action['id']))
    assert error.value.code == 'CONFIRMATION_REQUIRED' and service.chat.sent == []


def approve(service, action): service.store.approve(action['id'], action['review_hash'])


def test_exact_approved_action_sends_once_and_cannot_be_prepared_again(service, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    action = prepare(service); approve(service, action)
    result = asyncio.run(service.execute(action['id']))
    assert result['status'] == 'sent' and service.chat.sent == ['您好，项目介绍']
    with pytest.raises(YupaoError): asyncio.run(service.execute(action['id']))
    with pytest.raises(YupaoError): prepare(service)
    assert len(service.chat.sent) == 1


@pytest.mark.parametrize('change', ['account', 'job', 'payload', 'expired'])
def test_approval_is_bound_to_account_job_and_body_and_expiry(service, monkeypatch, change):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    action = prepare(service); approve(service, action)
    if change == 'account': service.client.account = 'different-account'
    elif change == 'job': service.client.job['title'] = 'Different job'
    else:
        with service.store.connection() as db:
            if change == 'payload':
                payload = service.store.action(action['id'])['payload']; payload['message'] = 'changed after approval'
                db.execute('UPDATE actions SET payload=? WHERE id=?', (encoded(payload), action['id']))
            else: db.execute('UPDATE actions SET expires=? WHERE id=?', (time.time() - 1, action['id']))
    with pytest.raises(YupaoError): asyncio.run(service.execute(action['id']))
    assert service.chat.sent == []


def test_acknowledgement_survives_failed_history_check_and_resend_is_blocked(service, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    action = prepare(service); approve(service, action); service.chat.fail_history = True
    result = asyncio.run(service.execute(action['id']))
    assert result['status'] == 'outcome_unknown' and result['receipt']['acknowledged'] is True
    assert result['receipt']['message_id'] == 'message1'
    with pytest.raises(YupaoError): prepare(service)
    assert len(service.chat.sent) == 1


def test_concurrent_execution_uses_one_atomic_reservation(service, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    action = prepare(service); approve(service, action)
    async def run(): return await asyncio.gather(service.execute(action['id']), service.execute(action['id']), return_exceptions=True)
    results = asyncio.run(run())
    assert len(service.chat.sent) == 1 and sum(isinstance(x, YupaoError) for x in results) == 1


def test_wrong_job_association_cannot_create_a_send_action(service):
    service.chat.target_job = 'other-job'
    with pytest.raises(YupaoError): prepare(service)
    assert service.chat.sent == []


def test_application_permission_and_selected_resume_are_checked(service, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    service.client.available = False
    action = service.prepare_application('Job123', 'resume123')
    assert action['review']['can_execute'] is False
    approve(service, action)
    with pytest.raises(YupaoError): asyncio.run(service.execute(action['id']))
    assert service.client.app_calls == 0


def test_confirmed_application_is_single_and_account_bound(service, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    action = service.prepare_application('Job123', 'resume123'); approve(service, action)
    assert asyncio.run(service.execute(action['id']))['status'] == 'submitted'
    assert service.client.app_calls == 1


def test_mcp_decline_cannot_approve_or_send(service, monkeypatch):
    import yupao_mcp.server as server
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    monkeypatch.setattr(server, 'store', service.store); monkeypatch.setattr(server, 'actions', service)
    action = prepare(service)
    class Context:
        async def elicit(self, *args): return SimpleNamespace(action='decline', data=None)
    result = asyncio.run(server.yupao_execute_action(action['id'], Context()))
    assert result['error']['code'] == 'CONFIRMATION_DECLINED' and service.chat.sent == []
    assert service.store.action(action['id'])['status'] == 'awaiting_confirmation'


def test_first_contact_is_prepared_without_creating_conversation_and_requires_same_confirmation(service, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    action = asyncio.run(service.prepare_new_message('Job123', '项目介绍'))
    assert service.client.app_calls == 0 and action['review']['can_execute'] is True
    approve(service, action)
    result = asyncio.run(service.execute(action['id']))
    assert result['status'] == 'sent' and result['receipt']['conversation_initialized'] is True
    assert service.client.app_calls == 1 and service.chat.sent == ['项目介绍']
    with pytest.raises(YupaoError): asyncio.run(service.prepare_message('Job123', 'C2Cperson', '项目介绍'))


def test_new_target_resolution_failure_does_not_send_or_allow_retry(service, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    action = asyncio.run(service.prepare_new_message('Job123', '项目介绍')); approve(service, action)
    def fail(*args): raise YupaoError('JOB_CONVERSATION_MISMATCH', 'different target')
    service.client.resolve_contact_target = fail
    result = asyncio.run(service.execute(action['id']))
    assert result['status'] == 'outcome_unknown' and result['receipt']['conversation_initialized'] is True
    assert service.chat.sent == []
    with pytest.raises(YupaoError): asyncio.run(service.execute(action['id']))


def test_sdk_pre_dispatch_diagnostic_is_preserved_with_completed_chat_initialization(service, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    action = asyncio.run(service.prepare_new_message('Job123', '项目介绍')); approve(service, action)
    async def blocked(*args, **kwargs):
        raise YupaoError('SEND_NOT_STARTED', 'SDK not synchronized',
                         details={'sdk_error_code': 'SDK_SYNC_INCOMPLETE', 'sdk_send_invoked': False})
    service.chat.send_confirmed_text = blocked
    result = asyncio.run(service.execute(action['id']))
    assert result['receipt']['conversation_initialized'] is True
    assert result['receipt']['sdk_send_invoked'] is False
    assert result['receipt']['sdk_error_code'] == 'SDK_SYNC_INCOMPLETE'
    assert result['retry_allowed'] is False and service.chat.sent == []


def test_known_content_rejection_is_not_unknown_and_same_payload_cannot_repeat(service, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    action = prepare(service); approve(service, action)
    async def rejected(*args, **kwargs):
        raise YupaoError('MESSAGE_CONTENT_REJECTED', 'Platform rejected',
            details={'sdk_error_code': '80001', 'sdk_send_invoked': True, 'delivery_rejected': True})
    service.chat.send_confirmed_text = rejected
    result = asyncio.run(service.execute(action['id']))
    assert result['status'] == 'rejected' and result['receipt']['outcome_unknown'] is False
    assert result['retry_allowed'] is False
    with pytest.raises(YupaoError): prepare(service)
