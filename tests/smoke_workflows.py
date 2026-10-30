"""Live local-workspace and preview verification; real writes explicitly disabled."""
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
        raise SystemExit('Explicitly configure your authorized local session first.')
    material = Path(os.environ['YUPAO_MATCH_TEXT_FILE']).read_text(encoding='utf-8')
    params = StdioServerParameters(command=sys.executable, args=['-m', 'yupao_mcp'],
        env={**os.environ, 'YUPAO_ENABLE_WRITES': '0', 'PYTHONIOENCODING': 'utf-8'})
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            unpack(await session.call_tool('yupao_save_preset', {'name': '上海 Agent 开发', 'keywords': '大模型',
                'city': '上海', 'page_size': 5, 'required_terms': ['Agent']}))
            search = unpack(await session.call_tool('yupao_search_preset', {'name': '上海 Agent 开发'}))
            candidates = search['jobs'] + search['review_jobs']
            assert candidates, 'No matching live jobs; stop without starting a conversation.'
            key = candidates[0]['job_biz_id']
            saved = unpack(await session.call_tool('yupao_save_job', {'job_biz_id': key,
                'tags': ['待评估', 'Agent'], 'note': '完整JD已保存；仅准备评估和消息，尚未投递或开聊。'}))
            bundle = unpack(await session.call_tool('yupao_prepare_match', {'job_biz_id': key,
                'resume_text': material, 'profile_name': '本人三个AI项目介绍（非完整简历）'}))
            result = {'search': search, 'saved': saved, 'match_input': bundle,
                      'tools': names, 'messages_sent': 0, 'applications_submitted': 0}
            folder = Path(os.environ.get('YUPAO_DATA_DIR', '.local'))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'workflow-smoke.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'tool_count': len(names), 'matched_count': len(search['jobs']),
                'review_count': len(search['review_jobs']), 'excluded_count': len(search['excluded_jobs']),
                'selected': {'job_biz_id': key, 'title': saved['job']['title'], 'company': saved['job']['company']},
                'match_input_id': bundle['match_input_id'], 'writes_enabled': False,
                'messages_sent': 0, 'applications_submitted': 0}, ensure_ascii=False))


asyncio.run(main())
