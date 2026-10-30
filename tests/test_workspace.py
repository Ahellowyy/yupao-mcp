import copy

import pytest

from yupao_mcp.client import YupaoError
from yupao_mcp.store import Store
from yupao_mcp.workspace import Workspace, salary_range


class Jobs:
    def __init__(self):
        self.job = {'job_biz_id': 'Job123', 'job_id': '123', 'title': 'Agent工程师', 'company': '示例公司',
                    'tags': ['20-30K·13薪', '本科', '3-5年'], 'description': '要求使用 Python 开发 Agent。需本科。',
                    'is_closed': False}
    def detail(self, key):
        assert key == 'Job123'
        return copy.deepcopy(self.job)
    def search(self, *args):
        return {'jobs': [self.detail('Job123')], 'returned_count': 1}


@pytest.fixture
def workspace(tmp_path):
    return Workspace(Store(tmp_path), Jobs())


@pytest.mark.parametrize('label,expected', [('20-30K·13薪', (20000, 30000)), ('1.3-4万/月', (13000, 40000)),
    ('2-4万/月·14薪', (20000, 40000)), ('7千-1.4万/月', (7000, 14000)),
    ('8千-1.2万/月', (8000, 12000)), ('18000-25000元/月', (18000, 25000)),
    ('1.8–2.5万元/月·13薪', (18000, 25000)), ('18千-25千/月', (18000, 25000)),
    ('1000-2000元', None), ('30-50万/年·14薪', None),
    ('300-500元/天', None), ('30-50万/年', None), ('面议', None)])
def test_monthly_salary_is_not_confused_with_daily_or_annual(label, expected):
    assert salary_range([label]) == expected


def test_preset_persists_and_unknown_salary_is_separate(workspace):
    workspace.save_preset('AI', 'Agent', salary_min=25000, education='本科', experience_years=3)
    assert workspace.search_preset('AI')['jobs'][0]['job_biz_id'] == 'Job123'
    workspace.client.job['tags'] = ['面议', '本科', '3-5年']
    result = workspace.search_preset('AI')
    assert not result['jobs'] and result['review_jobs'][0]['filter_result']['status'] == 'review'
    assert Store(workspace.store.path.parent).get('preset', 'AI')['rules']['salary_min'] == 25000


@pytest.mark.parametrize('kwargs', [{'salary_min': 30000, 'salary_max': 20000}, {'page_size': 99},
    {'experience_years': float('nan')}, {'education': 'whatever'}, {'city': 'invalid'}])
def test_invalid_preset_conditions_are_rejected(workspace, kwargs):
    with pytest.raises(YupaoError): workspace.save_preset('AI', 'Agent', **kwargs)


def test_saved_job_deduplicates_and_keeps_notes(workspace):
    workspace.save_job('Job123', ['重点'], '需要确认经验要求')
    workspace.save_job('Job123')
    rows = workspace.list_jobs('重点')
    assert len(rows) == 1 and rows[0]['note'] == '需要确认经验要求'
    workspace.annotate_job('Job123', ['不合适'], '经验不符', 'excluded')
    assert workspace.list_jobs(status='excluded')[0]['tags'] == ['不合适']


def analysis(status='satisfied', quote='我使用 Python 开发 Agent。'):
    return {'summary': '技术项目匹配，完整工作经历仍需核对', 'criteria': [
        {'requirement': 'Python与Agent', 'weight': 5, 'hard': True, 'status': status,
         'jd_quote': '要求使用 Python 开发 Agent。', 'resume_quote': quote, 'explanation': '两份材料均有明确表述'}]}


def test_host_ai_evidence_is_validated_and_jd_updates_mark_analysis_stale(workspace):
    bundle = workspace.prepare_match('Job123', '我使用 Python 开发 Agent。', '项目材料')
    saved = workspace.record_match(bundle['match_input_id'], analysis())
    assert saved['match_score'] == 100 and saved['evidence_coverage'] == 100
    workspace.client.job['description'] += '要求5年经验。'
    workspace.save_job('Job123')
    assert workspace.list_matches()[0]['jd_stale'] is True


@pytest.mark.parametrize('field,value', [('jd_quote', '伪造的JD'), ('resume_quote', '伪造经历')])
def test_fabricated_evidence_is_rejected(workspace, field, value):
    bundle = workspace.prepare_match('Job123', '我使用 Python 开发 Agent。')
    result = analysis(); result['criteria'][0][field] = value
    with pytest.raises(YupaoError) as error: workspace.record_match(bundle['match_input_id'], result)
    assert error.value.code == 'EVIDENCE_MISMATCH'


def test_missing_resume_evidence_must_be_unknown(workspace):
    bundle = workspace.prepare_match('Job123', '仅提供项目名称。')
    with pytest.raises(YupaoError) as error:
        workspace.record_match(bundle['match_input_id'], analysis('not_satisfied', ''))
    assert error.value.code == 'EVIDENCE_REQUIRED'
    result = workspace.record_match(bundle['match_input_id'], analysis('unknown', ''))
    assert result['recommendation'] == 'review' and result['evidence_coverage'] == 0
