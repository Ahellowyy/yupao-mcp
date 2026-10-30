"""Filter the platform's activity labels, never invent a last-seen timestamp."""
import re
from typing import Literal

HrActivityFilter = Literal['any', 'just_now', 'today', 'within_3_days', 'within_7_days', 'within_30_days']
HR_ACTIVITY_FILTERS = {'any': '不限', 'just_now': '刚刚活跃', 'today': '当天活跃',
    'within_3_days': '3天内活跃', 'within_7_days': '7天内活跃', 'within_30_days': '30天内活跃'}


def check_activity(info, condition):
    """Return matched/excluded/unknown for an explicitly reported label window.

    A broader label is excluded because it cannot satisfy the requested label
    window, not because it proves the HR has been inactive for that duration.
    """
    if condition == 'any':
        return 'matched'
    label = info.get('activity_text') if isinstance(info, dict) else None
    if not isinstance(label, str) or not label.strip():
        return 'unknown'
    label = label.strip()
    if label == '刚刚活跃':
        return 'matched'
    same_day = label in ('今日活跃', '今天活跃', '当天活跃')
    calendar_days = {'昨日活跃': 2, '昨天活跃': 2, '前天活跃': 3}.get(label)
    days = re.fullmatch(r'([1-9]\d{0,3})天内活跃', label)
    months = re.fullmatch(r'([1-9]\d{0,2})个月内活跃', label)
    if not (same_day or calendar_days or days or months):
        # In particular, "当前未在线" says nothing about the last active date.
        return 'unknown'
    if condition == 'just_now':
        return 'excluded'
    if condition == 'today':
        # "1天内" is a rolling window, not necessarily the current calendar day.
        return 'matched' if same_day else 'excluded'
    limit = {'within_3_days': 3, 'within_7_days': 7, 'within_30_days': 30}[condition]
    if same_day:
        return 'matched'
    if calendar_days:
        return 'matched' if calendar_days <= limit else 'excluded'
    if months:
        # Do not turn calendar months into an invented exact number of days.
        return 'excluded'
    return 'matched' if int(days[1]) <= limit else 'excluded'
