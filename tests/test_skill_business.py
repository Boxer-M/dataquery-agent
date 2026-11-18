"""Skill context through real planning/validation/runtime; fixed models are explicit."""
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from dataquery_agent.api_models import public_response
from dataquery_agent.query_history import build_record
from dataquery_agent.skill_registry import ROOT, SkillRegistry
from test_business_query_planner import CASES as BUSINESS, proposal_output
from test_unified_agent import CASES, TODAY, fixed_generate, question

QUESTION = '分析2025年1月订单量和成功支付金额的多指标汇总'


@pytest.fixture
def fixture():
    from test_unified_agent import UnifiedAgentTests
    instance = UnifiedAgentTests()
    instance.setUp()
    instance.service.query.side_effect = lambda request: instance.fake_query(CASES['multi'], request)
    return instance


def model_for(case='multi', *, usage=True):
    return Mock(return_value={'text': proposal_output(BUSINESS[case]),
                              'usage': {'prompt': 100, 'output': 20, 'total': 120}}
                if usage else proposal_output(BUSINESS[case]))


def test_skill_is_only_in_existing_planning_call_and_usage_is_not_invented(fixture):
    model = model_for()
    with patch.object(SkillRegistry, 'load_skill_resource', side_effect=AssertionError('unselected resource')):
        answer = fixture.agent.answer(QUESTION, model, today=TODAY)
    assert answer['status'] == 'ok'
    prompt, = model.call_args.args
    assert prompt.startswith('[business-plan-v1]')
    assert json.dumps(SkillRegistry().load_skill_instructions('business-metric-analysis'), ensure_ascii=False) in prompt
    event, = answer['trace']['skills']
    assert {key: event[key] for key in ('skill_id', 'version', 'sha256')} == SkillRegistry().skill_identity('business-metric-analysis')
    assert event['loaded'] and event['context_injected']
    assert event['reason'] == 'explicit_complex_metric_analysis'
    assert event['planning_model_calls'] == model.call_count == 1
    assert event['planning_tokens'] == {'prompt': 100, 'output': 20, 'total': 120}
    assert event['extra_model_calls'] == 0 and event['extra_tokens'] is None
    assert fixture.service.query.call_count == 2
    assert len(answer['trace']['capability_records']) == 2


def test_selected_and_unselected_paths_keep_same_plan_and_tool_requests(fixture):
    selected = fixture.agent.answer(QUESTION, model_for(usage=False), today=TODAY)
    requests = [deepcopy(call.args[0]) for call in fixture.service.query.call_args_list]
    fixture.service.query.reset_mock()
    with patch.object(SkillRegistry, 'select_candidates', return_value=[]), patch.object(
            SkillRegistry, 'load_skill_instructions', side_effect=AssertionError('unselected body')):
        model = model_for(usage=False)
        baseline = fixture.agent.answer(QUESTION, model, today=TODAY)
    assert selected['result']['plan'] == baseline['result']['plan']
    assert selected['result']['results'] == baseline['result']['results']
    assert [call.args[0] for call in fixture.service.query.call_args_list] == requests
    assert model.call_count == 1 and not baseline['trace'].get('skills')
    assert selected['trace']['skills'][0]['planning_tokens'] is None


def test_simple_ambiguous_and_unsupported_paths_do_not_load_skills(fixture):
    for case_id in ('order', 'ambiguous_sales', 'profit'):
        case = CASES[case_id]
        model = Mock(side_effect=lambda prompt: fixed_generate(case, prompt))
        fixture.service.query.reset_mock()
        fixture.service.query.side_effect = lambda request: fixture.fake_query(case, request)
        with patch('dataquery_agent.agent_prompts.SkillRegistry', side_effect=AssertionError('unexpected registry')):
            answer = fixture.agent.answer(question(case), model, today=TODAY)
        assert answer['status'] == case['status']
        assert not answer['trace'].get('skills')
        assert model.call_count == (1 if case_id == 'order' else 0)


@pytest.mark.parametrize('case_id', ['invalid_group', 'step_limit', 'grain'])
def test_validator_still_rejects_unsupported_plans(fixture, case_id):
    case = BUSINESS[case_id]
    answer = fixture.agent.answer('分析' + case['question'] + '的多指标分析', model_for(case_id), today=TODAY)
    assert answer['code'] == case['expected']['code']
    assert answer['trace']['skills'][0]['context_injected']
    fixture.service.query.assert_not_called()


def test_execution_capability_denial_remains_authoritative(fixture):
    fixture.registry.snapshot.get('dws.payment_day')['columns']['attempt_amount']['metric_readable'] = False
    answer = fixture.agent.answer(QUESTION, model_for(), today=TODAY)
    assert answer['code'] == 'access_denied'
    assert answer['trace']['skills'][0]['loaded']
    fixture.service.query.assert_not_called()


def test_supervisor_uses_skill_only_in_planner_context(fixture):
    fixture.agent.supervisor = True
    prompts = []
    def generate(prompt):
        prompts.append(prompt)
        if prompt.startswith('[business-plan-v1]'):
            return proposal_output(BUSINESS['multi'])
        return json.dumps({'decision': 'data' if prompt.startswith('[supervisor-v1]') else 'execute'})
    answer = fixture.agent.answer(QUESTION, generate, today=TODAY)
    assert answer['status'] == 'ok'
    assert len(prompts) == 3
    assert 'instructions' in prompts[0] and all('instructions' not in prompt for prompt in prompts[1:])
    assert [task['agent_call']['agent'] for task in answer['result']['subtasks']] == ['data_analysis'] * 2
    assert {record['capability_phase'] for record in answer['trace']['capability_records']} == {
        'preflight', 'execution_recheck'}


@pytest.mark.parametrize('failure', ['tampered', 'disabled', 'unreviewed'])
def test_loading_failure_stops_before_model_or_sql(fixture, tmp_path, failure):
    shutil.copytree(ROOT / 'skills', tmp_path / 'skills')
    path = tmp_path / 'skills/registry.json'
    data = json.loads(path.read_text())
    if failure == 'tampered':
        (tmp_path / 'skills/business-metric-analysis/SKILL.md').write_text('unreviewed instructions')
    else:
        data['skills'][0].update(enabled=False)
        if failure == 'unreviewed':
            data['skills'][0]['review_status'] = 'unreviewed'
        path.write_text(json.dumps(data))
    model = model_for()
    with patch('dataquery_agent.agent_prompts.SkillRegistry', side_effect=lambda: SkillRegistry(tmp_path)):
        answer = fixture.agent.answer(QUESTION, model, today=TODAY)
    assert answer['code'] == 'skill_unavailable'
    event, = answer['trace']['skills']
    assert not event['loaded'] and not event['context_injected']
    assert event['planning_model_calls'] == 0
    model.assert_not_called()
    fixture.service.query.assert_not_called()


def test_runtime_retries_are_bounded_and_usage_unknown_when_attempt_did_not_report(fixture):
    model = model_for()
    model.side_effect = [TimeoutError('do not log this'), model.return_value]
    answer = fixture.agent.answer(QUESTION, model, today=TODAY)
    event, = answer['trace']['skills']
    assert answer['status'] == 'ok' and event['planning_model_calls'] == 2
    assert event['planning_tokens'] is None and event['extra_tokens'] is None
    assert 'do not log this' not in str(answer['trace'])


def test_skill_does_not_bypass_exhausted_model_budget(fixture):
    from dataquery_agent.agent_runtime import AgentRuntime
    model = model_for()
    runtime = AgentRuntime(model)
    runtime.attempts = [{'stage': 'intent'}] * 6
    trace = {'tools': []}
    result = fixture.agent.business(QUESTION, runtime, {'event_time_hint': None}, trace, TODAY)
    assert result['code'] == 'model_unavailable'
    assert trace['skills'][0]['loaded'] and not trace['skills'][0]['context_injected']
    assert trace['skills'][0]['planning_model_calls'] == 0
    model.assert_not_called()
    fixture.service.query.assert_not_called()


def test_history_and_api_preserve_skill_identity_but_not_instructions(fixture):
    answer = fixture.agent.answer(QUESTION, model_for(), today=TODAY)
    answer.update(query_id=str(uuid4()), conversation_id=str(uuid4()), message_id=str(uuid4()))
    claim = {key: answer[key] for key in ('conversation_id', 'message_id')}
    saved = build_record(claim, QUESTION, answer, 'new', 1, query_id=answer['query_id'])
    assert saved['skills'] == answer['trace']['skills']
    assert public_response(answer).trace == public_response(saved, history=True).trace
    corrupted = deepcopy(saved)
    corrupted['skills'][0].update(instructions='private', planning_tokens={'prompt': 100, 'secret': 10})
    public = public_response(corrupted, history=True).model_dump_json()
    assert 'private' not in public and 'secret' not in public


@pytest.mark.skipif(os.getenv('KNOWLEDGE_POSTGRES_TESTS') != '1', reason='PostgreSQL opt-in')
def test_postgres_skill_query_ods_reconciliation_and_persisted_trace():
    from dataquery_agent.conversation_manager import local_manager
    from dataquery_agent.conversation_store import ConversationError
    from evaluate_business_planning import reference_connection, reference_rows
    manager, owner = local_manager()
    manager.agent.supervisor = False
    manager.generate = model_for(usage=False)
    cid = manager.create_conversation(owner)['conversation_id']
    with patch.object(manager.agent.knowledge.retriever.embedding, 'embed', side_effect=AssertionError('external embedding')):
        answer = manager.process_message(owner, cid, QUESTION, uuid4(), today=TODAY)
    assert answer['status'] == 'ok'
    saved = manager.get_query(owner, answer['query_id'])
    assert saved['skills'] == answer['trace']['skills']
    assert public_response(answer).trace == public_response(saved, history=True).trace
    with reference_connection() as pg:
        expected = {call['request']['metric_id']: reference_rows(pg, call['request'])['all']['value']
                    for call in answer['result']['plan']['required_tool_calls']}
    actual = answer['result']['results'][0]['metrics']
    assert {mid: Decimal(actual[mid]['value']) for mid in expected} == expected
    with pytest.raises(ConversationError):
        manager.get_query('x' * 64, answer['query_id'])
    duplicate = manager.process_message(owner, cid, QUESTION, answer['message_id'], today=TODAY)
    assert duplicate['replayed'] and manager.generate.call_count == 1
    if destination := os.getenv('SKILL_BUSINESS_REPORT'):
        Path(destination).write_text(json.dumps({
            'mode': 'fixed model proposal + real PostgreSQL; no real Gemini',
            'question': QUESTION, 'ods_values': {mid: str(value) for mid, value in expected.items()},
            'actual_values': {mid: actual[mid]['value'] for mid in expected},
            'skill_trace': saved['skills'], 'public_trace_equal': True,
            'cross_owner_denied': True, 'duplicate_replayed': True,
            'proposal_callback_calls': manager.generate.call_count,
            'real_gemini_calls': 0, 'real_gemini_tokens': 0, 'query_embedding_calls': 0,
        }, ensure_ascii=False, indent=2) + '\n')
