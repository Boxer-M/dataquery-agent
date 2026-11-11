"""Bound model attempts and retain safe telemetry for every attempt."""
from time import monotonic

from .agent_prompts import PROMPT_VERSIONS

RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504}


class ModelUnavailable(RuntimeError):
    pass


class AgentRuntime:
    def __init__(self, generate):
        self.generate = generate
        self.attempts = []

    def call(self, stage, prompt):
        for attempt in (1, 2):
            if len(self.attempts) >= 6:
                break
            started = monotonic()
            event = {"stage": stage, "attempt": attempt, "prompt_version": PROMPT_VERSIONS[stage]}
            try:
                raw = self.generate(prompt)
                text = raw.get("text") if isinstance(raw, dict) else raw
                usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
                if isinstance(raw, dict) and isinstance(raw.get("model"), str):
                    event["model"] = raw["model"]
                if isinstance(usage, dict):
                    event["usage"] = {key: usage[key] for key in ("prompt", "output", "total")
                                      if type(usage.get(key)) is int}
                event["status"] = "received"
                return text
            except Exception as error:
                code = getattr(error, "code", None)
                event.update(status="error", error_type=type(error).__name__,
                             http_status=code if type(code) is int else None)
                if (event["http_status"] not in RETRYABLE_HTTP
                        and not isinstance(error, TimeoutError)
                        and type(error).__name__ not in {"ReadTimeout", "ConnectTimeout"}):
                    break
            finally:
                event["elapsed_ms"] = round((monotonic() - started) * 1000)
                self.attempts.append(event)
        raise ModelUnavailable("Model request failed or exhausted its attempt budget.")

    def invalid_output(self):
        self.attempts[-1]["status"] = "invalid_output"
