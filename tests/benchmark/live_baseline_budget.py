"""Local guard and telemetry around existing Gemini adapters."""
import json
from time import monotonic

from dataquery_agent.agent_prompts import PROMPT_VERSIONS
from dataquery_agent.rag.documents import ensure_safe
from dataquery_agent.rag.pdf_cli import MeasuredEmbedding
from evaluate_repository_embeddings import SENSITIVE

# Units are USD millionths. Reserve the documented max output including thinking.
MODEL = "gemini-3.5-flash-lite"
MODEL_INPUT_RESERVE = 18000
MODEL_OUTPUT_RESERVE = 65536
EMBED_INPUT_RESERVE = 40960
LIMITS = {"generation_calls": 110, "embedding_calls": 30,
          "reserved_tokens": 10_500_000, "reserved_usd": 20.0}
PRICE_URL = "https://ai.google.dev/gemini-api/docs/pricing"


class BaselineGuard(ValueError):
    pass


class Budget:
    def __init__(self, model):
        if model.removeprefix("models/") != MODEL:
            raise BaselineGuard("unpriced_model")
        self.events = []
        self.unavailable = set()
        self.reserved_tokens = 0
        self.reserved_usd = 0.0

    def call(self, kind, work, *, stage=None):
        if kind in self.unavailable:
            raise BaselineGuard("provider_region_unavailable")
        is_generation = kind == "generation"
        inputs = MODEL_INPUT_RESERVE if is_generation else EMBED_INPUT_RESERVE
        outputs = MODEL_OUTPUT_RESERVE if is_generation else 0
        amount = (inputs * (0.30 if is_generation else 0.15) + outputs * 2.50) / 1_000_000
        if (sum(event["kind"] == kind for event in self.events) >= LIMITS[kind + "_calls"]
                or self.reserved_tokens + inputs + outputs > LIMITS["reserved_tokens"]
                or self.reserved_usd + amount > LIMITS["reserved_usd"]):
            raise BaselineGuard("budget_exhausted")
        self.reserved_tokens += inputs + outputs
        self.reserved_usd += amount
        event = {"kind": kind, "stage": stage}
        self.events.append(event)
        started = monotonic()
        try:
            result = work()
        except Exception as error:
            code = getattr(error, "code", None)
            event.update(status="error", error_type=type(error).__name__, http_status=code if type(code) is int else None)
            if code == 400 and "User location is not supported" in str(error):
                event["reason"] = "provider_region_unavailable"
                self.unavailable.add(kind)
            raise
        else:
            event["status"] = "ok"
            if is_generation:
                event.update(usage=result.get("usage"), model=result.get("model"))
            return result
        finally:
            event["elapsed_ms"] = round((monotonic() - started) * 1000, 3)


def generator(backend, budget, approved_passages, blocked):
    def generate(prompt):
        stage = prompt.partition("]")[0].removeprefix("[")
        try:
            if stage not in {PROMPT_VERSIONS[key] for key in ("intent", "resolution", "planning", "knowledge")}:
                raise BaselineGuard("unapproved_prompt_stage")
            if len(prompt.encode()) > 12000 or SENSITIVE.search(prompt):
                raise BaselineGuard("external_scope_rejected")
            if stage == PROMPT_VERSIONS["knowledge"]:
                payload = json.loads(prompt.split("\n", 1)[1])
                if any((item["title"], item["text"]) not in approved_passages for item in payload["evidence"]):
                    raise BaselineGuard("unapproved_evidence_text")
            return budget.call("generation", lambda: backend(prompt), stage=stage)
        except BaselineGuard as error:
            blocked.append({"stage": stage, "reason": str(error)})
            raise
    return generate


class BaselineEmbedding(MeasuredEmbedding):
    def __init__(self, api_key, budget):
        super().__init__(api_key)
        self.budget = budget
        self.approved_documents = set()

    def embed(self, texts, *, query=False):
        if (not 1 <= len(texts) <= 20 or sum(len(text.encode()) for text in texts) > 10000
                or (not query and not set(texts).issubset(self.approved_documents))):
            raise BaselineGuard("unapproved_embedding_input")
        for text in texts:
            ensure_safe(text)
            if SENSITIVE.search(text):
                raise BaselineGuard("unapproved_embedding_input")
        stage = "query" if query else "index"
        result = self.budget.call("embedding", lambda: super(BaselineEmbedding, self).embed(texts, query=query), stage=stage)
        self.budget.events[-1]["token_counts"] = self.batches[-1]["token_counts"]
        return result


def estimate(events):
    cost, priced = 0.0, 0
    for event in events:
        if event["kind"] == "generation":
            usage = event.get("usage") or {}
            prompt, total = usage.get("prompt"), usage.get("total")
            if not isinstance(prompt, int) or not isinstance(total, int) or total < prompt:
                continue
            cost += (0.30 * prompt + 2.50 * (total - prompt)) / 1_000_000
        else:
            counts = event.get("token_counts") or []
            if not counts or not all(isinstance(count, int) for count in counts):
                continue
            cost += 0.15 * sum(counts) / 1_000_000
        priced += 1
    return {"estimated_standard_paid_usd": round(cost, 8) if priced else None, "priced_calls": priced,
            "unpriced_calls": len(events) - priced, "source": PRICE_URL}
