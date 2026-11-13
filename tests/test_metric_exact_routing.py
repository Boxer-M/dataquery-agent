import json
import os
import unittest
from copy import deepcopy
from datetime import date
from unittest.mock import Mock
from uuid import uuid4

from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.intent_semantic_resolver import ALIASES
from dataquery_agent.metric_entity import DEFINITION_ALIASES, MetricEntityResolver
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.rag.catalog_evidence import exact_definitions

TODAY = date(2026, 11, 13)


class MetricIdentityTests(unittest.TestCase):
    def test_exact_name_id_alias_and_ambiguity(self):
        resolver = MetricEntityResolver(MetricQueryService(None).metrics, ALIASES | DEFINITION_ALIASES)
        for phrase, method in (("ＩＴＥＭ＿ＲＥＴＵＲＮ＿ＲＡＴＥ", "metric_id"),
                               ("商品明细退货率", "formal_name"), ("商品退款率", "approved_alias")):
            result = resolver.resolve(phrase + "怎么定义？")
            self.assertEqual(result["metric_ids"], ["item_return_rate"])
            self.assertEqual(result["matches"][0]["method"], method)
        self.assertEqual(resolver.resolve("退货率")["status"], "ambiguous")
        self.assertEqual(resolver.resolve("other_item_return_rate")["status"], "not_found")

    def test_missing_index_does_not_fabricate_a_citation(self):
        store = Mock()
        store.catalog_chunks.return_value = []
        service = MetricQueryService(None)
        result = exact_definitions(store, service, {"metric_ids": ["item_return_rate"], "method": "metric_id"},
                                   today=TODAY)
        self.assertEqual(result["citations"], [])
        self.assertIn(service.metrics["item_return_rate"]["definition"], result["answer_zh"])
        self.assertEqual(result["retrieval_trace"]["reason"], "catalog_index_missing_or_stale")


@unittest.skipUnless(os.getenv("KNOWLEDGE_POSTGRES_TESTS") == "1", "PostgreSQL opt-in")
class ExactRoutingPostgresTests(unittest.TestCase):
    def test_routes_history_and_catalog_drift(self):
        manager, owner = local_manager()
        self.assertIsNotNone(manager.agent.knowledge)
        manager.generate = Mock(side_effect=AssertionError("unexpected generation"))
        retriever = manager.agent.knowledge.retriever
        retriever.embedding = Mock()
        retriever.embedding.embed.side_effect = AssertionError("unexpected query embedding")
        cid = manager.create_conversation(owner)["conversation_id"]
        cases = [
            ("item_return_rate", "metric_id", "supported"),
            ("商品明细退货率怎么定义？", "formal_name", "supported"),
            ("商品退款率怎么定义？", "approved_alias", "supported"),
            ("那订单退款率呢？", "not_found", "insufficient"),
            ("订单退货率怎么定义？", "not_found", "insufficient"),
            ("退货率怎么定义？", "ambiguous", "insufficient"),
            ("item_return_rate_v99 怎么定义？", "not_found", "insufficient"),
        ]
        report = []
        for question, method, status in cases:
            answer = manager.process_message(owner, cid, question, uuid4(), today=TODAY)
            history = manager.get_query(owner, answer["query_id"])["knowledge"]
            self.assertEqual(history["evidence_status"], status, answer)
            self.assertEqual(history["metric_resolution"]["method"], method)
            self.assertFalse(history["query_embedding_called"])
            if status == "supported":
                self.assertEqual(history["citations"][0]["quote"],
                                 manager.agent.service.metrics["item_return_rate"]["definition"])
            else:
                self.assertEqual(history["citations"], [])
            if method == "ambiguous":
                self.assertEqual(answer["status"], "clarify")
                self.assertEqual(len(answer["choices"]), 2)
            if question.startswith("那"):
                self.assertEqual(answer["context_operation"], "knowledge_followup")
            report.append({"question": question, "history": history, "trace": answer["trace"]})
        changed = deepcopy(manager.agent.service)
        changed.metrics["item_return_rate"]["definition"] += " 校验变更。"
        stale = exact_definitions(retriever.store, changed, {"method": "metric_id", "metric_ids": ["item_return_rate"]},
                                  today=TODAY)
        self.assertEqual(stale["citations"], [])
        self.assertIn("校验变更", stale["answer_zh"])
        self.assertEqual(stale["retrieval_trace"]["reason"], "catalog_index_missing_or_stale")
        hybrid = manager.process_message(owner, cid, "2025年1月退货相关指标是多少？对应的业务定义是什么？",
                                         uuid4(), today=TODAY)
        self.assertEqual(hybrid["task"], "hybrid")
        from evaluate_business_knowledge import truth_for_hybrid
        truth = truth_for_hybrid(hybrid)
        self.assertEqual(len(truth), 3)
        self.assertTrue(all(row["equal"] for row in truth.values()))
        self.assertEqual(len(hybrid["result"]["knowledge_result"]["citations"]), 3)
        retriever.embedding.embed.assert_not_called()
        manager.generate.assert_not_called()
        if path := os.getenv("METRIC_EXACT_REPORT"):
            from pathlib import Path
            Path(path).write_text(json.dumps({"cases": report, "stale_catalog": stale,
                                              "hybrid": hybrid, "hybrid_ods_truth": truth,
                                              "embedding_calls": 0, "generation_calls": 0},
                                             ensure_ascii=False, default=str, indent=2) + "\n")

    def test_document_question_still_calls_retriever(self):
        manager, _ = local_manager()
        search = Mock(return_value=([], {"trace_id": "test", "strategy": "pgvector-cosine-exact-v1",
                                         "query_embedding_called": True}))
        manager.agent.knowledge.retriever.search = search
        answer = manager.agent.answer("平台退款政策的规定是什么？", Mock(), today=TODAY)
        self.assertEqual(answer["result"]["knowledge_topic"], "document")
        self.assertTrue(search.call_args.kwargs["rules"])
