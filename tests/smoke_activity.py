"""Explicit live MCP activity-filter smoke test; no contact, messages or resumes."""
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
    assert value.get('ok'), value.get('error', {}).get('code')
    return value['data']


async def main():
    if not os.environ.get('YUPAO_SESSION_FILE'):
        raise SystemExit('Explicitly configure your authorized local session first.')
    folder = Path(os.environ.get('YUPAO_DATA_DIR', '.local/activity-smoke')).resolve()
    params = StdioServerParameters(command=sys.executable, args=['-m', 'yupao_mcp'],
        env={**os.environ, 'YUPAO_DATA_DIR': str(folder), 'YUPAO_ENABLE_WRITES': '0', 'PYTHONIOENCODING': 'utf-8'})
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            tool = next(t for t in tools if t.name == 'yupao_save_preset')
            assert 'within_3_days' in tool.inputSchema['properties']['hr_activity']['enum']
            preset = unpack(await session.call_tool('yupao_save_preset', {
                'name': '上海 Agent · HR 3天内活跃', 'keywords': 'Agent', 'city': '上海',
                'page_size': 5, 'required_terms': ['Agent'], 'hr_activity': 'within_3_days'}))
            result = unpack(await session.call_tool('yupao_search_preset', {'name': preset['name']}))
            assert result['search_returned_count'] == sum(len(result[k]) for k in ('jobs', 'review_jobs', 'excluded_jobs'))
            for row in result['jobs']:
                assert row.get('hr_activity', {}).get('activity_text')
                assert row['filter_result']['status'] == 'matched'
            record = {'result': result, 'tool_count': len(tools), 'messages_sent': 0, 'resumes_sent': 0}
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'activity-smoke-result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'tool_count': len(tools), 'condition': preset['rules']['hr_activity'],
                'matched': len(result['jobs']), 'review': len(result['review_jobs']),
                'excluded': len(result['excluded_jobs']), 'activity_checks': result['hr_activity_requests'],
                'matched_activity_labels': [r['hr_activity']['activity_text'] for r in result['jobs']],
                'messages_sent': 0, 'resumes_sent': 0}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
