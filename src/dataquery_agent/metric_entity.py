"""Resolve formal metric names without semantic similarity."""
import re
import unicodedata
from collections import defaultdict

# Knowledge-specific aliases accepted by the existing definition route.
DEFINITION_ALIASES = {
    "商品退款率": ("item_return_rate",),
    "商品退货率": ("item_return_rate",),
    "明细退货率": ("item_return_rate",),
    "有几条退货记录": ("return_record_count",),
}


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


class MetricEntityResolver:
    def __init__(self, metrics, aliases):
        self.terms = defaultdict(dict)
        for method, entries in (
            ("metric_id", ((mid, (mid,)) for mid in metrics)),
            ("formal_name", ((item["name_zh"], (mid,)) for mid, item in metrics.items())),
            ("approved_alias", aliases.items()),
        ):
            for term, ids in entries:
                for mid in ids:
                    if mid in metrics:
                        self.terms[normalize(term)].setdefault(mid, method)
        phrases = sorted(self.terms, key=lambda phrase: (-len(phrase), phrase))
        self.pattern = re.compile("|".join(
            (r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])")
            if term.isascii() else re.escape(term) for term in phrases
        ) or r"(?!)")

    def resolve(self, question):
        matches = []
        for term in dict.fromkeys(match.group() for match in self.pattern.finditer(normalize(question))):
            ids = self.terms[term]
            matches.append({"term": term, "metric_ids": list(ids),
                            "method": next(iter(ids.values())) if len(ids) == 1 else "ambiguous"})
        ids = list(dict.fromkeys(mid for match in matches for mid in match["metric_ids"]))
        return {"status": "ambiguous" if len(ids) > 1 else "unique" if ids else "not_found",
                "metric_ids": ids, "matches": matches}
