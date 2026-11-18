"""Pinned third-party text loading only: no spreadsheet libraries, network or execution."""
import hashlib
import json
import shutil
from unittest.mock import patch

import pytest

from dataquery_agent.agent_prompts import business_planning_call
from dataquery_agent.agent_runtime import AgentRuntime
from dataquery_agent.skill_registry import ROOT, SkillError, SkillRegistry

PIN = 'e4bb5d960d87126ff3b3a8af1a104b7db67560ba'


def test_pinned_source_license_and_retained_material_integrity():
    config = json.loads((ROOT / 'skills/registry.json').read_text())['skills'][1]
    assert config['upstream_commit'] == PIN and PIN in config['source']['location']
    assert config['license'] == 'Apache-2.0'
    assert config['review_status'] == 'reviewed' and config['enabled']
    assert config['allowed_tools'] == []
    provenance = json.loads((ROOT / 'skills/xlsx/UPSTREAM.json').read_text())
    assert provenance['commit'] == PIN and len(provenance['upstream_files']) == 53
    for original, local in [('skills/xlsx/SKILL.md', 'upstream/SKILL.md'),
                            ('skills/xlsx/LICENSE.txt', 'LICENSE.txt')]:
        record = next(item for item in provenance['upstream_files'] if item['path'] == original)
        raw = (ROOT / 'skills/xlsx' / local).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record['sha256']
        assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == record['git_blob_sha']
    notices = (ROOT / 'skills/xlsx/upstream/THIRD_PARTY_NOTICES.md').read_bytes()
    assert hashlib.sha256(notices).hexdigest() == provenance['repository_notices_sha256']
    assert not list((ROOT / 'skills/xlsx').rglob('*.py'))
    assert not list((ROOT / 'skills/xlsx').rglob('*.xsd'))


def test_approved_xlsx_and_exact_resource_whitelist():
    registry = SkillRegistry()
    assert registry.load_skill_instructions('xlsx') == (ROOT / 'skills/xlsx/SKILL.md').read_text()
    for name in ('format-guidance', 'provenance', 'license'):
        assert registry.load_skill_resource('xlsx', name)
    for name in ('scripts/recalc.py', 'upstream/SKILL.md', '../LICENSE.txt', 'UPSTREAM.json'):
        with pytest.raises(SkillError, match='unknown_skill_resource'):
            registry.load_skill_resource('xlsx', name)


@pytest.mark.parametrize('target,resource', [('SKILL.md', None),
                                          ('references/format-guidance.md', 'format-guidance'),
                                          ('NOTICE.md', 'provenance'), ('LICENSE.txt', 'license')])
def test_xlsx_tampering_refused(tmp_path, target, resource):
    shutil.copytree(ROOT / 'skills', tmp_path / 'skills')
    registry = SkillRegistry(tmp_path)
    path = tmp_path / 'skills/xlsx' / target
    path.write_text(path.read_text() + '\nchanged')
    with pytest.raises(SkillError, match='skill_hash_mismatch'):
        if resource:
            registry.load_skill_resource('xlsx', resource)
        else:
            registry.load_skill_instructions('xlsx')


@pytest.mark.parametrize('target,resource', [('SKILL.md', None), ('references', 'format-guidance')])
def test_xlsx_symlinks_refused(tmp_path, target, resource):
    shutil.copytree(ROOT / 'skills', tmp_path / 'skills')
    registry = SkillRegistry(tmp_path)
    path = tmp_path / 'skills/xlsx' / target
    outside = tmp_path / 'outside'
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=outside.is_dir())
    with pytest.raises(SkillError, match='skill_file_unavailable'):
        if resource:
            registry.load_skill_resource('xlsx', resource)
        else:
            registry.load_skill_instructions('xlsx')


def test_candidate_is_loadable_not_executable_and_never_auto_injected():
    runtime = AgentRuntime(lambda prompt: prompt)
    trace = {}
    with patch.object(SkillRegistry, 'load_skill_instructions', side_effect=AssertionError('automatic XLSX load')):
        assert SkillRegistry().select_candidates('请生成Excel经营报告')[0]['available'] is True
        callback = business_planning_call('请生成Excel经营报告', runtime, trace)
        assert callback('original planner context') == '[business-plan-v1] original planner context'
    assert not trace.get('skills')
    assert len(runtime.attempts) == 1  # Fixed local echo, not a Gemini call.
