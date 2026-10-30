import json

import httpx
import pytest

from yupao_mcp.client import YupaoClient, YupaoError, plain, normalize_job
from yupao_mcp.drafts import save_draft
from yupao_mcp.signing import sign_request, normalize


def test_signing_is_order_independent_and_covers_nested_values():
    a = {'keywords': '智能体', 'filters': {'z': [True, None, {'b': 2, 'a': 1}], 'a': 3}}
    b = {'filters': {'a': 3, 'z': [True, None, {'a': 1, 'b': 2}]}, 'keywords': '智能体'}
    assert sign_request(a, '1000', '7') == sign_request(b, '1000', '7')
    assert sign_request(a, '1001', '7') != sign_request(a, '1000', '7')
    assert normalize(1.0) == 1
    with pytest.raises(ValueError):
        normalize(float('nan'))


def test_search_is_bounded_and_uses_expected_request_format():
    calls = []
    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body['areaIds'] == [25] and body['keywords'] == 'Agent'
        assert request.headers['sign'] == sign_request(body, request.headers['timestamp'], request.headers['nonce'])
        return httpx.Response(200, json={'code': 0, 'data': {'total': 99, 'list': [
            {'jobId': 1, 'jobBizId': 'Abc123', 'title': '<b>Agent开发</b>',
             'userId': 123, 'maskMobile': '138****0000',
             'companyInfo': {'brandName': '示例公司'}, 'areaInfo': {'provinceName': '上海'}}]}})
    c = YupaoClient(transport=httpx.MockTransport(handler), interval=0)
    result = c.search('Agent', page_size=1)
    assert result['jobs'][0]['title'] == 'Agent开发'
    assert result['returned_count'] == 1 and result['server_reported_total'] == 99
    assert 'userId' not in result['jobs'][0] and 'maskMobile' not in result['jobs'][0]
    for kwargs in ({'page': 0}, {'page_size': 1000}, {'city': 'not-a-city'}):
        with pytest.raises(YupaoError):
            c.search('Agent', **kwargs)
    assert len(calls) == 1


def test_risk_or_rate_limit_stops_further_calls_without_retries():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'code': 1610002, 'message': '频繁操作'})
    c = YupaoClient(transport=httpx.MockTransport(handler), interval=0)
    with pytest.raises(YupaoError) as error:
        c.search('Agent')
    assert error.value.code == '1610002'
    with pytest.raises(YupaoError) as error:
        c.search('Agent')
    assert error.value.code == 'REQUESTS_STOPPED' and len(calls) == 1


def test_http_rate_limit_also_stops_followup_calls():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429)
    c = YupaoClient(transport=httpx.MockTransport(handler), interval=0)
    with pytest.raises(YupaoError):
        c.search('Agent')
    with pytest.raises(YupaoError) as error:
        c.search('Agent')
    assert error.value.code == 'REQUESTS_STOPPED' and len(calls) == 1


def test_arbitrary_or_write_endpoint_is_not_exposed():
    c = YupaoClient(transport=httpx.MockTransport(lambda r: pytest.fail('Network must not be called')), interval=0)
    with pytest.raises(YupaoError):
        c.post('submit', {'jobId': 1})
    with pytest.raises(YupaoError):
        c.detail('../private')


def test_detail_checks_identity_and_preserves_full_description():
    desc = '岗位要求\n' + ('Python、RAG、Agent。\n' * 300)
    c = YupaoClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        'code': 0, 'data': {'jobBizId': 'Abc123', 'jobId': 1, 'title': 'Agent', 'detail': desc}})), interval=0)
    assert c.detail('Abc123')['description'] == desc.strip()
    with pytest.raises(YupaoError):
        c.detail('WrongID')


def test_html_cleanup_and_public_tags():
    assert plain('<p>Python &amp; Agent</p><script>do_bad()</script><p>RAG</p>') == 'Python & Agent\n\nRAG'
    result = normalize_job({'jobId': 1, 'title': '测试', 'occShowTags': [
        {'showTags': [{'name': '20-30K'}, {'name': '本科'}]}],
        'urbanAreas': {'provinceName': '上海', 'cityName': '浦东新区'}})
    assert result['tags'] == ['20-30K', '本科'] and result['city'] == '上海浦东新区'


def test_drafts_are_local_and_explicitly_not_submitted(tmp_path):
    draft = save_draft(tmp_path, 'message', {'job_biz_id': 'Abc123'}, '您好，想了解岗位')
    stored = json.loads(next((tmp_path / 'drafts').glob('*.json')).read_text(encoding='utf-8'))
    assert stored == draft and not draft['submitted'] and not draft['message_sent']
    with pytest.raises(YupaoError):
        save_draft(tmp_path, 'message', {}, '')
    assert len(list((tmp_path / 'drafts').glob('*.json'))) == 1


def test_missing_session_does_not_claim_login_or_access_browser():
    c = YupaoClient(transport=httpx.MockTransport(lambda r: pytest.fail('No request expected')))
    assert c.check_session()['authenticated'] is False
