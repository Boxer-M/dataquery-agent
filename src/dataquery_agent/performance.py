"""Bound request work and cache authorized, versioned metric results."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from threading import BoundedSemaphore, Lock
from time import monotonic, perf_counter_ns, sleep
from uuid import uuid4


RATE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""
UNLOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""
CURRENT = ContextVar("dataquery_performance", default=None)


class WorkBusy(RuntimeError):
    pass


@dataclass
class RequestWork:
    control: "Performance"
    owner: str
    hits: int = 0
    misses: int = 0
    sql: int = 0
    acl: int = 0
    model_calls: int = 0
    model_tokens: int = 0
    durations: dict = field(default_factory=dict)

    def headers(self):
        headers = {f"X-DataQuery-{name.replace('_', '-')}": str(getattr(self, name))
                   for name in ("hits", "misses", "sql", "acl", "model_calls", "model_tokens")}
        headers.update({f"X-DataQuery-{stage}-ms": f"{elapsed / 1_000_000:.3f}"
                        for stage, elapsed in self.durations.items()})
        return headers


class Performance:
    def __init__(self, *, url=None, data_version=None, limit=0, window=10, inflight=16,
                 sql_inflight=8, model_inflight=4, ttl=60, report=False):
        self.redis = None
        self.redis_errors = ()
        if url:
            from redis import Redis
            from redis.exceptions import RedisError
            from redis.retry import Retry
            from redis.backoff import NoBackoff

            self.redis = Redis.from_url(url, socket_timeout=.15, socket_connect_timeout=.15,
                                        retry=Retry(NoBackoff(), 0), max_connections=32)
            self.redis_errors = (RedisError,)
        self.data_version, self.limit, self.window, self.ttl = data_version, limit, window, ttl
        self.report = report
        self.gates = {kind: BoundedSemaphore(size) for kind, size in
                      (("http", inflight), ("sql", sql_inflight), ("model", model_inflight))}
        self.rate_lock = Lock()
        self.rate_until = 0.0
        self.local_counts = {}

    @classmethod
    def local(cls):
        url = os.getenv("DATAQUERY_REDIS_URL")
        return cls(url=url, data_version=os.getenv("DATAQUERY_DATA_VERSION"),
                   limit=int(os.getenv("DATAQUERY_RATE_LIMIT", "60" if url else "0")),
                   report=os.getenv("DATAQUERY_PERF_HEADERS") == "1")

    def redis_command(self, method, *args, **kwargs):
        with timed('redis'):
            return getattr(self.redis, method)(*args, **kwargs)

    def admit(self, owner):
        if not self.limit:
            return 0
        if self.redis is not None:
            try:
                count, remaining = self.redis_command("eval", RATE_SCRIPT, 1, f"dq:rate:v1:{owner}:{self.window}", self.window)
                return max(1, remaining) if count > self.limit else 0
            except self.redis_errors:
                pass
        with self.rate_lock:
            now = monotonic()
            if now >= self.rate_until:
                self.local_counts.clear()
                self.rate_until = now + self.window
            remaining = max(1, int(self.rate_until - now + 1))
            if owner not in self.local_counts and len(self.local_counts) >= 4096:
                return remaining
            count = self.local_counts[owner] = self.local_counts.get(owner, 0) + 1
            return remaining if count > self.limit else 0

    @contextmanager
    def scope(self, owner):
        work = RequestWork(self, owner)
        token = CURRENT.set(work)
        try:
            yield work
        finally:
            CURRENT.reset(token)

    @contextmanager
    def slot(self, kind):
        gate = self.gates[kind]
        if not gate.acquire(blocking=False):
            raise WorkBusy(kind)
        try:
            yield
        finally:
            gate.release()

    def metric(self, work, service, request, route, execute):
        if self.redis is None or not self.data_version:
            return execute()
        service.validate(request)
        mid = request["metric_id"]
        normalized = dict(request, group_by=request.get("group_by", []), filters=request.get("filters", {}))
        material = [
            work.owner, normalized, self.data_version, service.cache_database_identity(),
            service.catalog["catalog_version"], service.metrics[mid], service.templates[mid],
            service.cutoff.isoformat(), route["metadata_version"], route["required_fields"],
        ]
        key = "dq:metric:v1:" + sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
        lock, token = key + ":load", uuid4().hex
        try:
            cached = self.redis_command("get", key)
            if not cached and self.redis_command("set", lock, token, nx=True, ex=20):
                try:
                    work.misses += 1
                    result = execute()
                    if result.rows is not None:
                        payload = json.dumps(result.result, ensure_ascii=False)
                        if len(payload.encode()) <= 256 * 1024:
                            try:
                                self.redis_command("set", key, payload, ex=self.ttl)
                            except self.redis_errors:
                                pass
                    return result
                finally:
                    try:
                        self.redis_command("eval", UNLOCK_SCRIPT, 1, lock, token)
                    except self.redis_errors:
                        pass
            if not cached:
                for _ in range(5):
                    sleep(.02)
                    cached = self.redis_command("get", key)
                    if cached:
                        break
                if not cached:
                    from .metric_tool import metric_failure
                    return metric_failure("cache_fill_busy")
        except self.redis_errors:
            return execute()
        try:
            result = json.loads(cached)
        except (ValueError, UnicodeError):
            return execute()
        if not isinstance(result, dict) or result.get("status") != "ok":
            return execute()
        access = service.cache_read_allowed(route["required_fields"])
        if access is False:
            from .metric_tool import metric_failure
            return metric_failure("cache_access_denied")
        if access is None:
            return execute()
        execution = execute(lambda _: dict(result, trace_id=uuid4().hex))
        if execution.rows is None:
            return execute()
        work.hits += 1
        return execution


@contextmanager
def slot(kind):
    work = CURRENT.get()
    if work is None:
        yield
    else:
        with work.control.slot(kind):
            yield


def count(field, value=1):
    if (work := CURRENT.get()) is not None:
        setattr(work, field, getattr(work, field) + value)


@contextmanager
def timed(stage):
    work = CURRENT.get()
    if work is None or not work.control.report:
        yield
        return
    started = perf_counter_ns()
    try:
        yield
    finally:
        work.durations[stage] = work.durations.get(stage, 0) + perf_counter_ns() - started
