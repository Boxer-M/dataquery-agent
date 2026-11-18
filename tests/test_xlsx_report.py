"""In-memory workbook generation/readback; fixed inputs separate from PostgreSQL."""
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from openpyxl import load_workbook
import pytest

from dataquery_agent.metrics import ROOT
from dataquery_agent.xlsx_report import generate_metric_xlsx


@pytest.fixture
def summary():
    return {'status': 'ok', 'plan': {
        'analysis_type': 'summary', 'metric_ids': ['order_count', 'captured_payment_amount'],
        'comparison_type': None, 'group_by': [],
        'time_windows': [{'start_date': '2025-01-01', 'end_date': '2025-02-01'}],
    }, 'results': [{'bucket': 'all', 'month': None, 'metrics': {
        'order_count': {'value': '7'}, 'captured_payment_amount': {'value': '12.30'}}}]}


@pytest.fixture
def catalog():
    return {item['metric_id']: item for item in json.loads((ROOT / 'metrics/warehouse_catalog_v1.json').read_text())['metrics']}


def test_readback_headers_order_numeric_types_and_display_scale(summary, catalog):
    original = deepcopy(summary)
    workbook = load_workbook(BytesIO(generate_metric_xlsx(summary, catalog)), data_only=True)
    assert workbook.sheetnames == ['经营指标']
    sheet = workbook.active
    assert (sheet.max_row, sheet.max_column) == (3, 2)
    assert list(sheet.values) == [('指标', '数值'), ('订单量', 7), ('成功支付金额', 12.3)]
    assert [sheet[f'B{i}'].data_type for i in (2, 3)] == ['n', 'n']
    assert [sheet[f'B{i}'].number_format for i in (2, 3)] == ['0', '0.00']
    workbook.close()
    assert summary == original
    summary['plan']['metric_ids'].reverse()
    reordered = load_workbook(BytesIO(generate_metric_xlsx(summary, catalog)), data_only=False)
    assert list(reordered.active.values)[1:] == [('成功支付金额', 12.3), ('订单量', 7)]
    assert all(cell.data_type != 'f' for row in reordered.active for cell in row)
    reordered.close()


@pytest.mark.parametrize('value', [None, True, 12.3, 'NaN', 'Infinity', '=1+1', 'invalid', '1234567890123456',
                                  '0.1234567890123456', '1e-100'])
def test_invalid_or_unsafe_precision_refused(summary, catalog, value):
    summary['results'][0]['metrics']['captured_payment_amount']['value'] = value
    with pytest.raises(ValueError):
        generate_metric_xlsx(summary, catalog)


@pytest.mark.parametrize('mutation', [
    lambda r: r.update(status='partial'),
    lambda r: r['plan'].update(comparison_type='period'),
    lambda r: r['plan'].update(group_by=['channel']),
    lambda r: r['plan']['time_windows'].append(r['plan']['time_windows'][0]),
    lambda r: r['results'].append(r['results'][0]),
    lambda r: r.update(rows_truncated=True),
    lambda r: r['results'][0]['metrics'].pop('order_count'),
])
def test_scope_does_not_silently_drop_business_meaning(summary, catalog, mutation):
    mutation(summary)
    with pytest.raises(ValueError):
        generate_metric_xlsx(summary, catalog)


def test_catalog_names_remain_text_not_formula(summary, catalog):
    catalog['order_count']['name_zh'] = '=1+1'
    book = load_workbook(BytesIO(generate_metric_xlsx(summary, catalog)), data_only=False)
    assert book.active['A2'].value == '=1+1' and book.active['A2'].data_type == 's'
    book.close()


@pytest.mark.skipif(os.getenv('KNOWLEDGE_POSTGRES_TESTS') != '1', reason='Real PostgreSQL opt-in')
def test_phase_b_real_query_to_workbook_and_ods_reconciliation():
    from dataquery_agent.conversation_manager import local_manager
    from evaluate_business_planning import reference_connection, reference_rows
    from test_skill_business import QUESTION, TODAY, model_for
    manager, owner = local_manager()
    manager.agent.supervisor = False
    manager.generate = model_for(usage=False)
    cid = manager.create_conversation(owner)['conversation_id']
    with patch.object(manager.agent.service, 'query', wraps=manager.agent.service.query) as query:
        answer = manager.process_message(owner, cid, QUESTION, uuid4(), today=TODAY)
        assert answer['status'] == 'ok'
        assert query.call_count == 2
        content = generate_metric_xlsx(answer['result'], manager.agent.service.metrics)
        assert query.call_count == 2  # Export does not run SQL.
    book = load_workbook(BytesIO(content), data_only=True)
    sheet = book.active
    assert list(sheet.values) == [('指标', '数值'), ('订单量', 2312), ('成功支付金额', 230864.57)]
    assert [sheet[f'B{i}'].data_type for i in (2, 3)] == ['n', 'n']
    assert sheet['B3'].number_format == '0.00'
    with reference_connection() as pg:
        expected = {call['request']['metric_id']: reference_rows(pg, call['request'])['all']['value']
                    for call in answer['result']['plan']['required_tool_calls']}
    mids = answer['result']['plan']['metric_ids']
    assert {mid: Decimal(str(sheet.cell(index, 2).value)) for index, mid in enumerate(mids, 2)} == expected
    assert manager.generate.call_count == 1  # Fixed proposal, never Gemini.
    if path := os.getenv('XLSX_READBACK_REPORT'):
        Path(path).write_text(json.dumps({
            'mode': 'fixed proposal + real PostgreSQL + in-memory XLSX readback',
            'actual_source_values': {mid: answer['result']['results'][0]['metrics'][mid]['value'] for mid in mids},
            'ods_values': {mid: str(value) for mid, value in expected.items()},
            'sheet': sheet.title, 'headers': [sheet['A1'].value, sheet['B1'].value],
            'rows': [{'metric_id': mid, 'name': sheet.cell(index, 1).value,
                      'value': str(sheet.cell(index, 2).value), 'type': sheet.cell(index, 2).data_type,
                      'number_format': sheet.cell(index, 2).number_format} for index, mid in enumerate(mids, 2)],
            'query_calls': query.call_count, 'export_query_calls': 0, 'real_gemini_calls': 0,
        }, ensure_ascii=False, indent=2) + '\n')
    book.close()
