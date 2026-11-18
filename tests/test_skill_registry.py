"""Offline skill discovery and filesystem trust boundaries."""
from copy import deepcopy
import json
import shutil
from unittest.mock import patch

import pytest

from dataquery_agent.skill_registry import ROOT, SkillError, SkillRegistry


@pytest.fixture
def workspace(tmp_path):
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    return tmp_path


def rewrite(root, mutate):
    path = root / "skills/registry.json"
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def rejected(code, action):
    with pytest.raises(SkillError) as caught:
        action()
    assert caught.value.code == code


def test_summaries_and_selection_never_open_instructions(workspace):
    original = SkillRegistry._read
    reads = []
    def observe(self, path, limit):
        reads.append(path)
        return original(self, path, limit)
    with patch.object(SkillRegistry, "_read", observe):
        registry = SkillRegistry(workspace)
        summaries = registry.list_skill_summaries()
        assert {item['name'] for item in summaries} == {'xlsx', 'business-metric-analysis'}
        assert all(set(item) == {'name', 'description', 'version'} for item in summaries)
        summaries[0]['name'] = 'changed'
        assert registry.list_skill_summaries()[0]['name'] == 'business-metric-analysis'
        assert registry.select_candidates('生成Excel经营报告')[0] == {
            'skill_id': 'xlsx', 'reason': 'explicit_excel_business_report', 'available': False}
        assert reads == ['skills/registry.json']
        assert '阶段 A' in registry.load_skill_instructions('business-metric-analysis')
        assert reads == ['skills/registry.json', 'skills/business-metric-analysis/SKILL.md']


def test_approved_resource_and_unknown_requests(workspace):
    registry = SkillRegistry(workspace)
    assert 'metric_tool.py' in registry.load_skill_resource('business-metric-analysis', 'contracts')
    rejected('unknown_skill', lambda: registry.load_skill_instructions('../other'))
    rejected('skill_unreviewed', lambda: registry.load_skill_instructions('xlsx'))
    rejected('unknown_skill_resource', lambda: registry.load_skill_resource('business-metric-analysis', '../SKILL.md'))
    rejected('unknown_skill', lambda: registry.load_skill_instructions([]))


@pytest.mark.parametrize('target', ['SKILL.md', 'references/contracts.md'])
def test_tampered_content_is_rejected_on_every_load(workspace, target):
    registry = SkillRegistry(workspace)
    registry.load_skill_instructions('business-metric-analysis')
    path = workspace / 'skills/business-metric-analysis' / target
    path.write_text(path.read_text() + '\nchanged')
    action = (lambda: registry.load_skill_instructions('business-metric-analysis')) if target == 'SKILL.md' else (
        lambda: registry.load_skill_resource('business-metric-analysis', 'contracts'))
    rejected('skill_hash_mismatch', action)


@pytest.mark.parametrize('path', ['../outside', '/tmp/SKILL.md',
                                'skills/business-metric-analysis/../SKILL.md',
                                'skills/business-metric-analysis//SKILL.md',
                                'skills/business-metric-analysis/./SKILL.md',
                                'skills/business-metric-analysis/\x00SKILL.md',
                                'skills/business-metric-analysis/\\SKILL.md'])
def test_unsafe_registered_path_rejected(workspace, path):
    rewrite(workspace, lambda d: d['skills'][0]['instructions'].update(path=path))
    rejected('unsafe_skill_path', lambda: SkillRegistry(workspace))


@pytest.mark.parametrize('target', ['SKILL.md', 'references', 'references/contracts.md'])
def test_symlink_components_rejected_even_when_target_hash_matches(workspace, target):
    registry = SkillRegistry(workspace)
    path = workspace / 'skills/business-metric-analysis' / target
    destination = workspace / 'outside'
    path.rename(destination)
    path.symlink_to(destination, target_is_directory=destination.is_dir())
    action = (lambda: registry.load_skill_instructions('business-metric-analysis')) if target == 'SKILL.md' else (
        lambda: registry.load_skill_resource('business-metric-analysis', 'contracts'))
    rejected('skill_file_unavailable', action)


def test_manifest_symlink_and_disabled_skill_rejected(workspace):
    path = workspace / 'skills/registry.json'
    destination = workspace / 'manifest.json'
    path.rename(destination)
    path.symlink_to(destination)
    rejected('skill_file_unavailable', lambda: SkillRegistry(workspace))
    path.unlink()
    destination.rename(path)
    rewrite(workspace, lambda d: d['skills'][0].update(enabled=False))
    rejected('skill_disabled', lambda: SkillRegistry(workspace).load_skill_instructions('business-metric-analysis'))


@pytest.mark.parametrize('mutation', [
    lambda d: d['skills'].append(deepcopy(d['skills'][0])),
    lambda d: d['skills'][0].update(skill_id='Invalid_Name'),
    lambda d: d['skills'][0].update(allowed_tools=['shell']),
    lambda d: d['skills'][1].update(enabled=True),
    lambda d: d['skills'][1].update(review_status='reviewed'),
    lambda d: d['skills'][0]['resources']['contracts'].update(sha256=None),
])
def test_invalid_or_unpinned_metadata_rejected(workspace, mutation):
    rewrite(workspace, mutation)
    rejected('invalid_skill_metadata', lambda: SkillRegistry(workspace))


@pytest.mark.parametrize('question', ['2025年1月订单量是多少？', '订单量怎么定义？', '分析经营表现',
                                    '不要生成Excel经营报告', '如何生成Excel经营报告？', '做点分析'])
def test_simple_or_uncertain_requests_keep_existing_path(question):
    assert SkillRegistry().select_candidates(question) == []


def test_explicit_complex_candidate_does_not_execute_or_load():
    registry = SkillRegistry()
    with patch.object(registry, '_read', side_effect=AssertionError('unexpected body loading')):
        assert registry.select_candidates('分析订单和支付的多指标趋势') == [{
            'skill_id': 'business-metric-analysis', 'reason': 'explicit_complex_metric_analysis', 'available': True}]


def test_oversized_resource_rejected_before_reading(workspace):
    path = workspace / 'skills/business-metric-analysis/references/contracts.md'
    path.write_text('x' * 65537)
    rejected('invalid_skill_file', lambda: SkillRegistry(workspace).load_skill_resource(
        'business-metric-analysis', 'contracts'))


def test_symlink_skill_root_directory_is_rejected(workspace):
    path = workspace / 'skills'
    path.rename(workspace / 'outside')
    path.symlink_to(workspace / 'outside', target_is_directory=True)
    rejected('skill_file_unavailable', lambda: SkillRegistry(workspace))


def test_existing_single_metric_does_not_instantiate_skill_registry():
    # Existing deterministic fixtures, not a real SQL/Gemini acceptance result.
    from test_unified_agent import UnifiedAgentTests, CASES, TODAY, fixed_generate, question
    fixture = UnifiedAgentTests()
    fixture.setUp()
    case = CASES['order']
    fixture.service.query.side_effect = lambda request: fixture.fake_query(case, request)
    with patch.object(SkillRegistry, '__init__', side_effect=AssertionError('Fast Path loaded skills')):
        result = fixture.agent.answer(question(case), lambda prompt: fixed_generate(case, prompt), today=TODAY)
    assert result['status'] == 'ok'
    assert fixture.service.query.call_count == 1
