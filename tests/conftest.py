"""An explicit CI guard against accidental paid model calls."""
import os

import pytest


@pytest.fixture(autouse=True)
def block_external_models_in_ci(monkeypatch):
    if os.getenv("CI_NO_EXTERNAL_MODELS") == "1":
        def denied(*args, **kwargs):
            pytest.fail("External Gemini client creation is forbidden in deterministic CI")
        monkeypatch.setattr("google.genai.Client", denied)
