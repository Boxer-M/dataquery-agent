"""Retrieve catalog candidates and independently verify selected paraphrases."""
import json
import re
from dataclasses import dataclass

# Confirmation is limited to auditable concept combinations, independent of model confidence.
ANCHORS = {
    "order_count": (r"下单|订单", r"几笔|几单|多少笔|多少单|有几笔"),
    "captured_payment_amount": (r"成功扣款|扣款成功|成功付款|付款成功", r"多少钱|金额|款项"),
    "payment_attempt_count": (r"尝试支付|支付尝试|发起支付", r"几次|几回|多少次"),
    "payment_failed_attempt_rate": (r"支付|付款", r"失败", r"比例|比重|占几成"),
}

BLOCKERS = {
    "order_count": r"取消|未付款|已付款|有效|成交|退款",
    "captured_payment_amount": r"未成功|没有成功|不成功|没成功|失败|退款|退货|退回|到账|结算",
    "payment_attempt_count": r"成功|失败|每单|重试",
    "payment_failed_attempt_rate": r"订单比例|每单|退款",
}


@dataclass(frozen=True)
class MetricBinding:
    metric_id: str
    evidence: str


def grams(text):
    text = "".join(re.findall(r"[\u4e00-\u9fffA-Za-z]+", text.lower()))
    return {text[index:index + 2] for index in range(len(text) - 1)}


class MetricCandidates:
    def __init__(self, resolver):
        self.metrics = resolver.service.metrics
        self.aliases = {}
        for phrase, mids in resolver.names.items():
            for mid in mids:
                if phrase not in (mid, self.metrics[mid]["name_zh"]):
                    self.aliases.setdefault(mid, []).append(phrase)

    def anchored(self, evidence):
        return {mid for mid, rules in ANCHORS.items() if mid in self.metrics
                and all(re.search(rule, evidence) for rule in rules)
                and not re.search(BLOCKERS[mid], evidence)}

    def shortlist(self, evidence, limit=6):
        tokens = grams(evidence)
        anchors = self.anchored(evidence)
        ranked = []
        for mid, metric in self.metrics.items():
            aliases = self.aliases.get(mid, [])
            vocabulary = grams(" ".join([metric["name_zh"], metric["definition"], *aliases]))
            name_tokens = grams(metric["name_zh"])
            name_hits = len(tokens & name_tokens)
            score = len(tokens & vocabulary)
            if score or mid in anchors:
                ranked.append((mid not in anchors, -name_hits, len(name_tokens) if name_hits else 0, -score, mid))
        return [
            {"metric_id": mid, "name": self.metrics[mid]["name_zh"],
             "definition": self.metrics[mid]["definition"], "aliases": self.aliases.get(mid, [])}
            for _, _, _, _, mid in sorted(ranked)[:limit]
        ]

    def validate(self, raw, evidence, candidates):
        if not isinstance(raw, str) or len(raw) > 4096:
            raise ValueError("Invalid metric proposal.")
        proposal = json.loads(raw)
        if not isinstance(proposal, dict) or set(proposal) != {"metric_ids", "evidence"}:
            raise ValueError("Invalid metric proposal schema.")
        ids = proposal["metric_ids"]
        allowed = {item["metric_id"] for item in candidates}
        if (proposal["evidence"] != evidence or not isinstance(ids, list)
                or any(not isinstance(mid, str) or mid not in allowed for mid in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("Metric proposal is not grounded in the shortlist.")
        anchors = self.anchored(evidence)
        binding = MetricBinding(ids[0], evidence) if len(ids) == 1 and anchors == set(ids) else None
        return ids, binding
