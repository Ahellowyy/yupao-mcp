import copy
import json

import httpx
import pytest

from yupao_mcp.client import YupaoClient, YupaoError, require_free_precheck


@pytest.fixture
def context():
    return {'job': {'job_id': '123', 'job_biz_id': 'Job123'}, 'ordinary_job': True, 'open': True,
            'attachment_allowed': True, 'free_contact_available': True, 'already_applied': False, 'occ_v2': []}


def make_client(tmp_path, handler):
    path = tmp_path / 'session.json'
    path.write_text(json.dumps({'token': 'test-token', 'userrole': 2}))
    return YupaoClient(str(path), transport=httpx.MockTransport(handler), interval=0)


def test_ordinary_attachment_flow_has_fixed_payload_and_checks_readback(tmp_path, context, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    paths = []
    def handler(request):
        paths.append(request.url.path)
        body = json.loads(request.content)
        if request.url.path.endswith('/preCheck'):
            assert body == {'jobId': 123, 'scene': 1, 'occV2': [], 'postType': 1, 'chatType': 2, 'notPopCodeList': []}
            data = {}
        elif request.url.path.endswith('/call'): data = {'callTel': 'opaque-marker-not-exported'}
        elif request.url.path.endswith('/init'):
            assert body == {'infoType': 1, 'infoId': 123, 'fromType': 9, 'postType': 1, 'resumeAttachUuid': 'resume123'}
            data = {'conversationId': 'business123'}
        else:
            data = {'jobId': 123, 'jobBizId': 'Job123', 'aboutInfoSpecifics': {
                'postInfo': {'hasPost': True, 'postType': 1}}}
        return httpx.Response(200, json={'code': 0, 'data': data})
    result = make_client(tmp_path, handler).submit_attachment_application(context, 'resume123')
    assert result['submitted'] is True and 'opaque-marker' not in json.dumps(result)
    assert len(paths) == 4 and paths[-1].endswith('/detail/info')


def test_precheck_rejection_does_not_continue(tmp_path, context, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'code': 10000, 'message': '需要平台确认'})
    with pytest.raises(YupaoError): make_client(tmp_path, handler).submit_attachment_application(context, 'resume123')
    assert len(calls) == 1


def test_disabled_or_paid_or_unsupported_application_never_requests(tmp_path, context, monkeypatch):
    c = make_client(tmp_path, lambda r: pytest.fail('No request allowed'))
    monkeypatch.delenv('YUPAO_ENABLE_WRITES', raising=False)
    with pytest.raises(YupaoError): c.submit_attachment_application(context, 'resume123')
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    for key in ('ordinary_job', 'attachment_allowed', 'free_contact_available', 'open'):
        changed = copy.deepcopy(context); changed[key] = False
        with pytest.raises(YupaoError): c.submit_attachment_application(changed, 'resume123')


def test_write_result_without_application_readback_is_unknown(tmp_path, context, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    def handler(request):
        if request.url.path.endswith('/call'): data = {'callTel': 'opaque'}
        elif request.url.path.endswith('/init'): data = {'conversationId': 'business123'}
        elif request.url.path.endswith('/info'):
            data = {'jobBizId': 'Job123', 'jobId': 123, 'aboutInfoSpecifics': {'postInfo': {'hasPost': False}}}
        else: data = {}
        return httpx.Response(200, json={'code': 0, 'data': data})
    result = make_client(tmp_path, handler).submit_attachment_application(context, 'resume123')
    assert result['submitted'] is None and result['acknowledged'] is True


def test_first_contact_omits_resume_and_contact_exchange_fields(tmp_path, context, monkeypatch):
    monkeypatch.setenv('YUPAO_ENABLE_WRITES', '1')
    context['already_contacted'] = False
    paths = []
    def handler(request):
        body = json.loads(request.content); paths.append(request.url.path)
        if request.url.path.endswith('/init'):
            assert body == {'infoType': 1, 'infoId': 123}
            data = {'conversationId': 'business123'}
        else:
            assert 'postType' not in body and 'resumeAttachUuid' not in body and 'wechatVal' not in body
            data = {'callTel': 'opaque'} if request.url.path.endswith('/call') else {}
        return httpx.Response(200, json={'code': 0, 'data': data})
    assert make_client(tmp_path, handler).initiate_conversation(context) == 'business123'
    assert len(paths) == 3


def test_masked_publisher_identity_can_bind_a_preview(tmp_path):
    def handler(request):
        return httpx.Response(200, json={'code': 0, 'data': {'jobBizId': 'Job123', 'jobId': 123,
            'userId': 0, 'userIdHash': 'public-opaque-publisher', 'tenant': 'YPZP', 'recruitType': 1,
            'isWhiteCollar': True, 'isGather': False, 'isX': False, 'isEnd': {'code': 1}}})
    context = make_client(tmp_path, handler).job_action_context('Job123')
    assert context['owner_key'] == 'public-opaque-publisher' and context['ordinary_job'] is True


@pytest.mark.parametrize('price', [{'discountPrice': 1}, {'unknownPrice': 1}, {'discountPrice': 'NaN'}, {'discountPrice': False}])
def test_nonfree_or_unknown_quote_cannot_continue(price):
    with pytest.raises(YupaoError): require_free_precheck({'priceInfo': price})


def test_zero_price_is_recognized_without_spending():
    require_free_precheck({'priceInfo': {'discountPrice': '0.00'}})


def test_first_contact_receiver_must_match_business_conversation_and_job(tmp_path):
    def handler(request):
        if request.url.path.endswith('/detailV2'): data = {'toUserImId': 'person1'}
        else: data = {'list': [{'toUserImId': 'person1', 'conversationId': 'business123', 'relatedJobId': 123}]}
        return httpx.Response(200, json={'code': 0, 'data': data})
    c = make_client(tmp_path, handler)
    assert c.resolve_contact_target('business123', '123')['conversation_id'] == 'C2Cperson1'
    with pytest.raises(YupaoError): c.resolve_contact_target('business123', 'different-job')
    with pytest.raises(YupaoError): c.resolve_contact_target('different-business', '123')
