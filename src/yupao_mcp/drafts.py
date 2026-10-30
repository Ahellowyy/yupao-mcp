"""Local previews only. This module cannot submit jobs or publish messages."""
import hashlib
import json
from pathlib import Path
import time
import uuid

from .client import YupaoError


def save_draft(data_dir: Path, kind: str, job: dict, text: str = ''):
    if kind not in ('application', 'message'):
        raise YupaoError('INVALID_ARGUMENT', '未知预览类型')
    if kind == 'message' and (not text.strip() or len(text) > 5000):
        raise YupaoError('INVALID_ARGUMENT', '消息正文须为 1 至 5000 字')
    preview = {'draft_id': uuid.uuid4().hex, 'kind': kind, 'status': 'draft_only',
               'job': job, 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
               'created_at': time.time(), 'submitted': False, 'message_sent': False,
               'next_step': '实际投递/发送尚未启用；需本人登录、接通写入适配并完成明确确认。'}
    folder = data_dir / 'drafts'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (preview['draft_id'] + '.json')).write_text(
        json.dumps(preview, ensure_ascii=False, indent=2), encoding='utf-8')
    return preview
