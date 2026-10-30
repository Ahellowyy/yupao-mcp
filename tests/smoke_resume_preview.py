"""Explicit live MCP preview only. Never approves, sends, downloads or marks read."""
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def unpack(result):
    assert not result.isError, 'MCP protocol failure'
    return result.structuredContent or json.loads(result.content[0].text)


async def main():
    required = ['YUPAO_SESSION_FILE', 'YUPAO_PREVIEW_JOB', 'YUPAO_PREVIEW_CONVERSATION', 'YUPAO_PREVIEW_RESUME']
    if any(not os.environ.get(k) for k in required):
        raise SystemExit('Configure your authorized session and explicit preview target first.')
    params = StdioServerParameters(command=sys.executable, args=['-m', 'yupao_mcp'],
        env={**os.environ, 'YUPAO_ENABLE_WRITES': '0', 'PYTHONIOENCODING': 'utf-8'})
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            assert 'yupao_prepare_resume' in names
            result = unpack(await session.call_tool('yupao_prepare_resume', {
                'job_biz_id': os.environ['YUPAO_PREVIEW_JOB'],
                'conversation_id': os.environ['YUPAO_PREVIEW_CONVERSATION'],
                'resume_file_id': os.environ['YUPAO_PREVIEW_RESUME']}))
            assert result.get('ok'), result.get('error', {}).get('code')
            preview = result['data']
            assert preview['status'] == 'awaiting_confirmation' and preview['resume_sent'] is False
            denied = unpack(await session.call_tool('yupao_execute_action', {'action_id': preview['id']}))
            assert denied['error']['code'] == 'WRITES_DISABLED'
            folder = Path(os.environ.get('YUPAO_DATA_DIR', '.local'))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'resume-preview-smoke.json').write_text(json.dumps({
                'preview': preview, 'write_denied': denied, 'tools': names,
                'resumes_sent': 0, 'messages_sent': 0, 'marked_read': False},
                ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'tool_count': len(names), 'can_execute': preview['review']['can_execute'],
                'blockers': preview['review']['blockers'], 'write_error': denied['error']['code'],
                'resumes_sent': 0, 'messages_sent': 0, 'marked_read': False}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
