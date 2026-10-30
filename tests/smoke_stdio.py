"""Explicit live read-only MCP round-trip; never executes a remote write."""
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]

def unpack(result):
    assert not result.isError, 'MCP tool returned an error'
    return result.structuredContent or json.loads(result.content[0].text)


async def main():
    parameters = StdioServerParameters(command=sys.executable, args=['-m', 'yupao_mcp'],
        env={**os.environ, 'YUPAO_DATA_DIR': str(ROOT / '.local/smoke'), 'PYTHONIOENCODING': 'utf-8'})
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            caps = unpack(await session.call_tool('yupao_capabilities', {}))
            search = unpack(await session.call_tool('yupao_search_jobs',
                {'keywords': '大模型', 'city': '上海', 'page_size': 3}))
            assert search.get('ok'), search
            assert search['data']['jobs'], 'No live results to validate detail'
            key = search['data']['jobs'][0]['job_biz_id']
            detail = unpack(await session.call_tool('yupao_get_job_detail', {'job_biz_id': key}))
            assert detail.get('ok') and detail['data']['description'], detail
            draft = unpack(await session.call_tool('yupao_prepare_message',
                {'job_biz_id': key, 'text': '仅本地草稿测试，不发送。'}))
            assert draft.get('ok') and draft['data']['message_sent'] is False
            result = {'mcp_initialized': True, 'tools': names, 'capabilities': caps,
                'live_search_count': search['data']['returned_count'],
                'job_titles': [j['title'] for j in search['data']['jobs']],
                'detail_title': detail['data']['title'],
                'detail_chars': len(detail['data']['description']),
                'local_draft_created': True, 'applications_submitted': 0, 'messages_sent': 0}
            (ROOT / '.local').mkdir(exist_ok=True)
            (ROOT / '.local/live-smoke-result.json').write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False))


asyncio.run(main())
