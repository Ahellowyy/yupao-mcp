"""Explicit account read verification over MCP. No remote write or SDK login."""
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def unpack(result):
    assert not result.isError, 'MCP protocol error'
    value = result.structuredContent or json.loads(result.content[0].text)
    assert value.get('ok'), value.get('error', {}).get('code', 'UNKNOWN')
    return value['data']


async def main():
    if not os.environ.get('YUPAO_SESSION_FILE'):
        raise SystemExit('Set YUPAO_SESSION_FILE to your explicitly authorized local session file.')
    params = StdioServerParameters(command=sys.executable, args=['-m', 'yupao_mcp'],
                                  env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = [tool.name for tool in (await session.list_tools()).tools]
            account = unpack(await session.call_tool('yupao_check_session', {}))
            resumes = unpack(await session.call_tool('yupao_list_resume_attachments', {}))
            chat = unpack(await session.call_tool('yupao_list_resume_attachments', {'for_chat': True}))
            messaging = unpack(await session.call_tool('yupao_check_messaging_session', {}))
            assert account['authenticated'] is True
            assert messaging['credentials_available'] is True
            assert messaging['sdk_login_verified'] is False and messaging['send_verified'] is False
            result = {'mcp_initialized': True, 'tool_count': len(names),
                      'authenticated': True, 'application_resume_count': resumes['count'],
                      'chat_resume_count': chat['count'], 'im_credentials_available': True,
                      'sdk_login_verified': False, 'applications_submitted': 0, 'messages_sent': 0}
            folder = Path(os.environ.get('YUPAO_DATA_DIR', '.local'))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'authenticated-mcp-smoke.json').write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False))


asyncio.run(main())
