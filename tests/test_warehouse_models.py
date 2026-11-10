import copy
import json
import tempfile
import unittest
from pathlib import Path

import sqlglot
from dataquery_agent.dwd import TYPES
from dataquery_agent.metrics import ROOT, read_queries
from dataquery_agent.warehouse import MANIFEST, load_models


class WarehouseModelTests(unittest.TestCase):
    def test_complete_batch_has_one_reuse_only_model_and_no_new_dependencies(self):
        models = load_models()['models']
        self.assertEqual(len(models), 9)
        self.assertEqual(sum(m['name'].startswith('dim.') for m in models), 5)
        self.assertEqual([m['name'] for m in models if m['reuse_only']], ['dwd.order_item_fact'])
        item = next(m for m in models if m['reuse_only'])
        self.assertEqual({c['name']:c['type'] for c in item['columns']}, TYPES)

    def test_missing_dependency_and_duplicate_names_are_rejected(self):
        for duplicate in (False, True):
            manifest = copy.deepcopy(json.loads(MANIFEST.read_text()))
            if duplicate:
                manifest['models'][1]['name'] = manifest['models'][0]['name']
            else:
                manifest['models'][0]['depends_on'] = ['dwd.order_fact']
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'models.json'
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    load_models(path)

    def test_business_selects_and_ddl_are_separate_and_have_no_destructive_statements(self):
        for model in load_models()['models']:
            projection = sqlglot.parse((ROOT/model['transform']).read_text(), read='postgres')
            self.assertEqual(len(projection), 1)
            self.assertIsInstance(projection[0], sqlglot.exp.Query)
            ddl = sqlglot.parse((ROOT/model['ddl']).read_text(), read='postgres')
            self.assertEqual(len(ddl), 1)
            self.assertIsInstance(ddl[0], sqlglot.exp.Create)
            self.assertFalse(projection[0].find(sqlglot.exp.Delete))
        customer = (ROOT/'sql/warehouse/transform/dim/customer.sql').read_text()
        self.assertNotIn('email',customer)

    def test_existing_item_projection_and_eight_metric_ids_are_preserved(self):
        old = read_queries(ROOT/'sql/dwd/order_item_fact.sql')['projection']
        new = (ROOT/'sql/warehouse/transform/dwd/order_item_fact.sql').read_text()
        self.assertEqual(sqlglot.parse_one(old, read='postgres'),sqlglot.parse_one(new, read='postgres'))
        source = read_queries(ROOT/'sql/metrics/ecommerce_v1.sql')
        target = read_queries(ROOT/'sql/warehouse/quality/metrics.sql')
        self.assertEqual(set(source),set(target))
        self.assertEqual(len(target),8)
