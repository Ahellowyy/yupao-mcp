"""Explicit live SDK read through MCP. Preserves unread state; never sends."""
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
        raise SystemExit('Set YUPAO_SESSION_FILE to your authorized local session file.')
    params = StdioServerParameters(command=sys.executable, args=['-m', 'yupao_mcp'],
                                  env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            all_rows = unpack(await session.call_tool('yupao_list_conversations', {'include_system': True}))
            contacts = unpack(await session.call_tool('yupao_list_conversations', {}))
            history = None
            for row in all_rows['conversations']:
                if row['type'] == 'C2C':
                    history = unpack(await session.call_tool('yupao_get_messages', {
                        'conversation_id': row['conversation_id']}))
                    break
            after = unpack(await session.call_tool('yupao_list_conversations', {'include_system': True}))
            before_unread = {r['conversation_id']: r['unread_count'] for r in all_rows['conversations']}
            after_unread = {r['conversation_id']: r['unread_count'] for r in after['conversations']}
            summary = {'mcp_initialized': True, 'tool_count': len(tools), 'sdk_ready': True,
                       'is_sync_completed': all_rows['is_sync_completed'],
                       'sdk_available_count': all_rows['sdk_available_count'],
                       'contact_count': contacts['contact_count'],
                       'system_or_agent_count': all_rows['system_or_agent_count'],
                       'history_read': history is not None,
                       'history_page_count': len(history['messages']) if history else 0,
                       'history_is_completed': history['is_completed'] if history else None,
                       'unread_counts_unchanged': before_unread == after_unread,
                       'messages_sent': 0, 'resumes_sent': 0, 'marked_read': False}
            folder = Path(os.environ.get('YUPAO_DATA_DIR', '.local'))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'chat-mcp-result.json').write_text(json.dumps(
                {'summary': summary, 'conversations': all_rows, 'contacts': contacts, 'history': history},
                ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(summary, ensure_ascii=False))


asyncio.run(main())
