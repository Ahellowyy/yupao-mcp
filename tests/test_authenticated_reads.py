import json

import httpx
import pytest

from yupao_mcp.client import YupaoClient, YupaoError


@pytest.fixture
def session_file(tmp_path):
    path = tmp_path / 'session.json'
    path.write_text(json.dumps({'token': 'test-token', 'userrole': '2'}))
    return str(path)


@pytest.mark.parametrize('operation', [
    'profile', 'resume_attachments', 'chat_resume_attachments', 'im_credentials',
])
def test_authenticated_reads_require_explicit_session(monkeypatch, operation):
    monkeypatch.delenv('YUPAO_SESSION_FILE', raising=False)
    client = YupaoClient(transport=httpx.MockTransport(lambda r: pytest.fail('No request allowed')))
    with pytest.raises(YupaoError) as error:
        client.post(operation, {})
    assert error.value.code == 'LOGIN_REQUIRED'


@pytest.mark.parametrize('payload,code', [
    ([], 'SESSION_FILE_INVALID'),
    ({'token': 'test-token', 'userrole': 1}, 'JOBSEEKER_ROLE_REQUIRED'),
    ({'token': 'bad\ntoken'}, 'SESSION_FILE_INVALID'),
])
def test_invalid_sessions_fail_before_network(tmp_path, payload, code):
    path = tmp_path / 'session.json'
    path.write_text(json.dumps(payload))
    client = YupaoClient(session_file=str(path), transport=httpx.MockTransport(
        lambda r: pytest.fail('Invalid session must not access network')))
    with pytest.raises(YupaoError) as error:
        client.check_session()
    assert error.value.code == code


def test_default_role_is_jobseeker_and_profile_requires_identity(tmp_path):
    path = tmp_path / 'session.json'
    path.write_text(json.dumps({'token': 'test-token'}))
    def handler(request):
        assert request.headers['userrole'] == '2'
        assert request.headers['token'] == 'test-token'
        return httpx.Response(200, json={'code': 0, 'data': {'unexpected': True}})
    client = YupaoClient(session_file=str(path), transport=httpx.MockTransport(handler), interval=0)
    with pytest.raises(YupaoError) as error:
        client.check_session()
    assert error.value.code == 'SCHEMA_CHANGED'


@pytest.mark.parametrize('for_chat,endpoint', [
    (False, '/resume/v3/attach/myAttach'), (True, '/resume/v3/attach/myImAttach'),
])
def test_resume_list_preserves_identity_and_status_but_omits_urls(session_file, for_chat, endpoint):
    def handler(request):
        assert request.url.path == endpoint
        return httpx.Response(200, json={'code': 0, 'data': {'fileList': [
            {'uuid': 'example-resume', 'fileName': '示例简历', 'fileType': 'pdf',
             'fileSize': 1024, 'checkStatus': 2, 'finishStatus': 1,
             'fileUrl': 'https://private.example/resume.pdf?secret=download',
             'previewUrl': 'https://private.example/preview', 'userId': 123},
            {'uuid': 'rejected-resume', 'checkStatus': 3},
        ]}})
    client = YupaoClient(session_file=session_file, transport=httpx.MockTransport(handler), interval=0)
    result = client.list_resume_attachments(for_chat)
    assert result['count'] == 2
    assert result['files'][0]['resume_file_id'] == 'example-resume'
    assert result['files'][1]['rejected_by_platform'] is True
    assert result['sent'] is False
    output = json.dumps(result)
    assert 'private.example' not in output and 'userId' not in output


def test_im_status_never_returns_credentials_or_claims_sdk_login(session_file):
    client = YupaoClient(session_file=session_file, interval=0, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={'code': 0, 'data': {
            'imAccount': 'private-account', 'imToken': 'private-user-sig', 'extra': 'private-extra'}})))
    result = client.check_messaging_session()
    assert result['credentials_available'] is True
    assert result['sdk_login_verified'] is False and result['send_verified'] is False
    assert 'private-' not in json.dumps(result)


def test_authenticated_errors_do_not_echo_provider_secrets(session_file):
    client = YupaoClient(session_file=session_file, interval=0, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={'code': 9001, 'message': 'token=test-token; private'})))
    with pytest.raises(YupaoError) as error:
        client.check_messaging_session()
    assert error.value.code == '9001' and 'test-token' not in error.value.message


@pytest.mark.parametrize('data', [{'fileList': {}}, {'fileList': [None]}, {'fileList': [{}]}])
def test_changed_resume_schema_fails_closed(session_file, data):
    client = YupaoClient(session_file=session_file, interval=0, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={'code': 0, 'data': data})))
    with pytest.raises(YupaoError) as error:
        client.list_resume_attachments()
    assert error.value.code == 'SCHEMA_CHANGED'
