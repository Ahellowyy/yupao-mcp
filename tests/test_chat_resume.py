"""No live writes: verified target, human approval, attachment identity and readback."""
import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from yupao_mcp.actions import ActionService
from yupao_mcp.client import YupaoClient, YupaoError
from yupao_mcp.store import Store
from test_actions import Client, Chat


def card(mid='resume-message', file_id='resume123', subtype='3', **extra):
    return {'message_id': mid, 'flow': 'out', 'to': 'person', 'status': 'success', 'custom_type': '710.1',
            'resume_card': {'resume_file_id': file_id, 'sub_type': subtype}, **extra}


class ResumeClient(Client):
    def __init__(self):
        super().__init__()
        self.allowed = True
        self.resume_calls = []
        self.after_send = lambda: None
    def chat_resume_context(self, target):
        return {'can_send': self.allowed, 'blockers': [] if self.allowed else ['没有附件权限']}
    def send_chat_resume(self, target, file_id):
        self.resume_calls.append((target, file_id))
        self.after_send()
        return {'acknowledged': True, 'resume_file_id': file_id, 'resume_sent': False, 'delivery_verified': False}


class ResumeChat(Chat):
    def __init__(self):
        super().__init__()
        self.messages = []
    async def get_messages(self, cid, next_req_message_id=''):
        if self.fail_history: raise YupaoError('NETWORK_ERROR', 'private')
        return {'messages': copy.deepcopy(self.messages), 'is_completed': True, 'next_req_message_id': ''}


@pytest.fixture
def service(tmp_path):
    s = ActionService(Store(tmp_path), ResumeClient(), ResumeChat())
    s.client.after_send = lambda: s.chat.messages.append(card())
    return s


def prepare(s): return asyncio.run(s.prepare_resume('Job123', 'C2Cperson', 'resume123'))


def approve(s, action): s.store.approve(action['id'], action['review_hash'])


def test_preview_cannot_send_and_names_exact_resume_and_recipient(service):
    a = prepare(service)
    assert a['review']['resume']['resume_file_id'] == 'resume123'
    assert a['review']['target']['name'] == '示例 HR'
    assert a['requires_human_confirmation'] and not a['resume_sent']
    assert not service.client.resume_calls and not service.chat.sent


def test_disabled_unapproved_and_human_decline_never_send_resume(service, monkeypatch):
    import yupao_mcp.server as server
    a = prepare(service)
    monkeypatch.delenv('YUPAO_ENABLE_WRITES', raising=False)
    with pytest.raises(YupaoError): asyncio.run(service.execute(a['id']))
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    with pytest.raises(YupaoError): asyncio.run(service.execute(a['id']))
    monkeypatch.setattr(server, 'actions', service)
    monkeypatch.setattr(server, 'store', service.store)
    class Context:
        async def elicit(self, *args): return SimpleNamespace(action='decline', data=None)
    assert asyncio.run(server.yupao_execute_action(a['id'], Context()))['error']['code'] == 'CONFIRMATION_DECLINED'
    assert not service.client.resume_calls


def test_confirmed_resume_only_sends_once_and_reads_exact_new_card(service, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    a = prepare(service); approve(service, a)
    async def run():
        return await asyncio.gather(service.execute(a['id']), service.execute(a['id']), return_exceptions=True)
    results = asyncio.run(run())
    result = next(r for r in results if isinstance(r, dict))
    assert result['status'] == 'sent' and result['receipt']['resume_sent'] is True
    assert result['receipt']['confirmed_in_history'] and not result['receipt']['delivery_verified']
    assert len(service.client.resume_calls) == 1 and not service.chat.sent and service.client.app_calls == 0
    with pytest.raises(YupaoError): prepare(service)


@pytest.mark.parametrize('change', ['account', 'job', 'resume', 'permission', 'association', 'manual_share'])
def test_preflight_rejects_changes_and_manual_send_since_preview(service, monkeypatch, change):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    a = prepare(service); approve(service, a)
    if change == 'account': service.client.account = 'another'
    elif change == 'job': service.client.job['title'] = 'another'
    elif change == 'resume': service.client.resume['name'] = 'replacement'
    elif change == 'permission': service.client.allowed = False
    elif change == 'association': service.chat.target_job = 'another'
    else: service.chat.messages.append(card())
    with pytest.raises(YupaoError): asyncio.run(service.execute(a['id']))
    assert not service.client.resume_calls


def test_existing_manual_resume_and_pending_request_block_preview(service):
    for existing in [card(), card(file_id='', subtype='1')]:
        service.chat.messages = [existing]
        a = prepare(service)
        assert not a['review']['can_execute']
        assert '不重复发送' in ' '.join(a['review']['blockers'])


def test_chat_attachment_must_be_own_and_approved(service):
    service.client.resume['check_status'] = 1
    with pytest.raises(YupaoError): prepare(service)
    service.client.resume['check_status'] = 2
    with pytest.raises(YupaoError): asyncio.run(service.prepare_resume('Job123', 'C2Cperson', 'not-mine'))
    assert not service.client.resume_calls


def test_duplicate_in_older_page_is_found_and_bad_pagination_fails_closed(service):
    async def paged(cid, next_req_message_id=''):
        if not next_req_message_id:
            return {'messages': [], 'is_completed': False, 'next_req_message_id': 'older'}
        return {'messages': [card()], 'is_completed': True, 'next_req_message_id': ''}
    service.chat.get_messages = paged
    assert not prepare(service)['review']['can_execute']
    async def stuck(cid, next_req_message_id=''):
        return {'messages': [], 'is_completed': False, 'next_req_message_id': 'same'}
    service.chat.get_messages = stuck
    with pytest.raises(YupaoError) as e: prepare(service)
    assert e.value.code == 'HISTORY_INCOMPLETE'


@pytest.mark.parametrize('after', ['no_card', 'wrong_file', 'incoming', 'revoked', 'pending', 'failed_card', 'history_error', 'timeout'])
def test_acknowledgement_is_not_delivery_and_unknown_results_never_retry(service, monkeypatch, after):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    def response():
        if after == 'wrong_file': service.chat.messages.append(card(file_id='different'))
        elif after == 'incoming': service.chat.messages.append(card(flow='in'))
        elif after == 'revoked': service.chat.messages.append(card(is_revoked=True))
        elif after == 'pending': service.chat.messages.append(card(subtype='1'))
        elif after == 'failed_card': service.chat.messages.append(card(status='fail'))
        elif after == 'history_error': service.chat.fail_history = True
        elif after == 'timeout': raise YupaoError('NETWORK_ERROR', 'private URL')
    service.client.after_send = response
    a = prepare(service); approve(service, a)
    result = asyncio.run(service.execute(a['id']))
    assert result['status'] == ('submitted' if after == 'pending' else 'outcome_unknown')
    assert not result['receipt'].get('resume_sent')
    if after != 'timeout': assert result['receipt']['acknowledged']
    assert result['retry_allowed'] is False
    with pytest.raises(YupaoError): asyncio.run(service.execute(a['id']))
    assert len(service.client.resume_calls) == 1


def test_unrecognized_or_missing_attachment_metadata_stops_before_send(service):
    for item in [card(file_id=''), card(subtype='unknown'), card(resume_card=None)]:
        service.chat.messages = [item]
        with pytest.raises(YupaoError) as e: prepare(service)
        assert e.value.code == 'RESUME_HISTORY_UNKNOWN'
    assert not service.client.resume_calls


def make_client(tmp_path, handler):
    p = tmp_path / 'session.json'
    p.write_text(json.dumps({'token': 'test-only', 'userrole': 2}))
    return YupaoClient(str(p), transport=httpx.MockTransport(handler), interval=0)


TARGET = {'business_conversation_id': 'business123', 'peer_im_id': 'person', 'related_job_id': '123'}


def test_resume_business_endpoint_uses_exact_fields_and_redacts_response(tmp_path, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == '/reach/v2/im/exchange/applyExchange'
        assert json.loads(request.content) == {'conversationId': 'business123',
            'exchangeType': 'EXCHANGE_RESUME_FILE', 'extData': {'resumeFileId': 'resume123'}}
        return httpx.Response(200, json={'code': 0, 'data': {'fileUrl': 'private-signed-download'}})
    c = make_client(tmp_path, handler)
    monkeypatch.delenv('YUPAO_ENABLE_WRITES', raising=False)
    with pytest.raises(YupaoError): c.send_chat_resume(TARGET, 'resume123')
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    result = c.send_chat_resume(TARGET, 'resume123')
    assert len(calls) == 1 and result['acknowledged'] and not result['resume_sent']
    assert 'private' not in json.dumps(result)
    with pytest.raises(YupaoError): c.post('chat_resume_send', {})
    assert len(calls) == 1


@pytest.mark.parametrize('change', ['', 'peer', 'job', 'business', 'blocked', 'rights', 'paid', 'status', 'duplicate_right'])
def test_chat_rights_and_identity_are_checked_without_writing(tmp_path, change):
    d = {'conversationId': 'business123', 'toUserImId': 'person', 'relatedJobId': 123,
         'isBlock': False, 'toUserBlock': False, 'rightsStatusInfo': [
             {'type': 'sendMsg', 'value': True, 'hasImRight': 1},
             {'type': 'exchangeResumeFile', 'value': True, 'status': 1}]}
    if change == 'peer': d['toUserImId'] = 'other'
    elif change == 'job': d['relatedJobId'] = 456
    elif change == 'business': d['conversationId'] = 'other'
    elif change == 'blocked': d['isBlock'] = True
    elif change == 'rights': d['rightsStatusInfo'] = []
    elif change == 'paid': d['rightsStatusInfo'][0]['hasImRight'] = 0
    elif change == 'status': d['rightsStatusInfo'][1]['status'] = 0
    elif change == 'duplicate_right': d['rightsStatusInfo'].append(d['rightsStatusInfo'][1])
    def handler(request):
        assert request.url.path == '/reach/v2/im/chat/detailV2'
        return httpx.Response(200, json={'code': 0, 'data': d})
    c = make_client(tmp_path, handler)
    if change in ('peer', 'job', 'business'):
        with pytest.raises(YupaoError): c.chat_resume_context(TARGET)
    else: assert c.chat_resume_context(TARGET)['can_send'] is (change == '')
