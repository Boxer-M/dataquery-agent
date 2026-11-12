"""Gemini text retrieval vectors, with a versioned normalization contract."""
from math import isfinite, sqrt

from dotenv import dotenv_values

from ..metrics import ROOT
from .documents import KnowledgeError, ensure_safe

DIMENSIONS = 768


def vector(values):
    if len(values) != DIMENSIONS or any(not isfinite(x) for x in values):
        raise KnowledgeError("invalid_embedding")
    norm = sqrt(sum(x * x for x in values))
    if norm == 0 or not isfinite(norm):
        raise KnowledgeError("invalid_embedding")
    return "[" + ",".join(format(x / norm, ".9g") for x in values) + "]"


class GeminiEmbedding:
    model = "gemini-embedding-001"
    version = "gemini-embedding-001-768-unit-retrieval-v1"

    def __init__(self, api_key):
        self.api_key = api_key

    @classmethod
    def local(cls):
        return cls(dotenv_values(ROOT / ".env").get("GEMINI_API_KEY"))

    def embed(self, texts, *, query=False):
        from google import genai
        from google.genai import types

        if not texts or not self.api_key or len(texts) > 100:
            raise KnowledgeError("embedding_unavailable")
        for text in texts:
            ensure_safe(text)
        output = []
        with genai.Client(api_key=self.api_key, http_options=types.HttpOptions(timeout=15000)) as client:
            for start in range(0, len(texts), 20):
                batch = texts[start:start + 20]
                result = client.models.embed_content(
                    model=self.model, contents=batch,
                    config=types.EmbedContentConfig(output_dimensionality=DIMENSIONS,
                                                  task_type="RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"),
                )
                if not result.embeddings or len(result.embeddings) != len(batch):
                    raise KnowledgeError("invalid_embedding_response")
                output.extend(vector(item.values) for item in result.embeddings)
        return output
