import asyncio
import json

import httpx
import pytest

from yupao_mcp.chat import ChatReader, SYSTEM_CONVERSATIONS
from yupao_mcp.client import YupaoClient, YupaoError


def client():
    return YupaoClient(transport=httpx.MockTransport(lambda r: pytest.fail('No HTTP call expected')))


@pytest.mark.parametrize('operation', ['sendMessage', 'setMessageRead', 'deleteConversation'])
def test_write_operations_rejected_before_sdk_or_credentials(operation):
    reader = ChatReader(client())
    with pytest.raises(YupaoError) as error:
        asyncio.run(reader._invoke(operation, {}))
    assert error.value.code == 'SDK_OPERATION_NOT_ALLOWED'


@pytest.mark.parametrize('conversation', ['GROUPabc', '../private', '', 'C2Cabc<script>'])
def test_only_existing_c2c_identity_format_can_reach_sdk(conversation):
    reader = ChatReader(client())
    with pytest.raises(YupaoError):
        asyncio.run(reader.get_messages(conversation))


def test_tampered_sdk_is_not_executed(tmp_path):
    file = tmp_path / 'index.js'
    file.write_text('malicious replacement')
    reader = ChatReader(client(), str(file))
    with pytest.raises(YupaoError) as error:
        asyncio.run(reader.list_conversations())
    assert error.value.code == 'SDK_INTEGRITY_MISMATCH'


def test_system_and_agent_contacts_are_not_counted_as_hr():
    reader = ChatReader(client())
    async def invoke(operation, args):
        return {'is_sync_completed': True, 'conversations': [
            {'conversation_id': sorted(SYSTEM_CONVERSATIONS)[0], 'type': 'C2C', 'name': ''},
            {'conversation_id': 'C2Cagent_official', 'peer_im_id': 'agent_official', 'type': 'C2C'},
        ]}
    reader._invoke = invoke
    result = asyncio.run(reader.list_conversations())
    assert result['sdk_available_count'] == 2 and result['contact_count'] == 0
    assert result['conversations'] == [] and result['marked_read'] is False
    all_rows = asyncio.run(reader.list_conversations(include_system=True))
    assert all_rows['conversations'][0]['category'] == 'system_assistant'


def test_contacts_are_enriched_by_matching_peer_without_overwriting_sdk_identity():
    reader = ChatReader(client())
    async def invoke(operation, args):
        return {'is_sync_completed': True, 'conversations': [
            {'conversation_id': 'C2Cperson1', 'peer_im_id': 'person1', 'name': 'SDK昵称', 'type': 'C2C'},
        ]}
    def metadata(ids):
        assert ids == ['person1']
        return [{'peer_im_id': 'person1', 'business_conversation_id': 'business-123',
                 'name': '示例 HR', 'company': '示例公司', 'job_title': 'Agent工程师'}]
    reader._invoke = invoke
    reader.client.get_chat_metadata = metadata
    result = asyncio.run(reader.list_conversations())
    row = result['conversations'][0]
    assert row['conversation_id'] == 'C2Cperson1'
    assert row['business_conversation_id'] == 'business-123' and row['metadata_status'] == 'verified'


def test_message_page_must_match_requested_conversation():
    reader = ChatReader(client())
    async def invoke(operation, args):
        return {'conversation_id': 'C2Cwrong', 'messages': []}
    reader._invoke = invoke
    with pytest.raises(YupaoError) as error:
        asyncio.run(reader.get_messages('C2Cexpected'))
    assert error.value.code == 'SDK_SCHEMA_CHANGED'


def test_each_message_must_match_the_requested_conversation():
    reader = ChatReader(client())
    async def invoke(operation, args):
        return {'conversation_id': 'C2Cexpected', 'messages': [{'conversation_id': 'C2Cwrong'}],
                'is_completed': True, 'next_req_message_id': ''}
    reader._invoke = invoke
    with pytest.raises(YupaoError) as error:
        asyncio.run(reader.get_messages('C2Cexpected'))
    assert error.value.code == 'SDK_SCHEMA_CHANGED'


def test_sdk_content_rejection_is_reported_as_definite_rejection(monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    reader = ChatReader(client())
    async def ready(): pass
    async def close(): pass
    class Page:
        async def evaluate(self, *args):
            return {'ok': False, 'code': '80001', 'send_invoked': True, 'planned_message_id': 'example-message'}
    reader._ensure_ready = ready
    reader._close_resources = close
    reader._page = Page()
    with pytest.raises(YupaoError) as error:
        asyncio.run(reader.send_confirmed_text('C2Cperson', 'approved text'))
    assert error.value.code == 'MESSAGE_CONTENT_REJECTED'
    assert error.value.details['delivery_rejected'] is True
    assert error.value.details['sdk_error_code'] == '80001'


def test_chat_metadata_does_not_expose_unrequested_identity_or_contact_details(tmp_path):
    session = tmp_path / 'session.json'
    session.write_text(json.dumps({'token': 'test-token', 'userrole': 2}))
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'code': 0, 'data': {'list': [
            {'toUserImId': 'person1', 'toUserName': '示例 HR', 'conversationId': 'business-123',
             'tel': 'private-phone', 'fileUrl': 'private-resume-url',
             'jobShowInfo': {'jobEnterpriseName': '示例公司', 'jobOccName': 'Agent工程师'}}]}})
    c = YupaoClient(str(session), transport=httpx.MockTransport(handler), interval=0)
    rows = c.get_chat_metadata(['person1'])
    assert rows[0]['company'] == '示例公司' and 'private-' not in json.dumps(rows)
    with pytest.raises(YupaoError):
        c.get_chat_metadata(['unrequested'])
    for ids in ([], ['person1'] * 51, ['bad/path']):
        with pytest.raises(YupaoError):
            c.get_chat_metadata(ids)
    assert len(calls) == 2
