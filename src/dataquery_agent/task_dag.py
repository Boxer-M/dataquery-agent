"""Sequential scheduling for validated metric and evidence tasks."""
from dataclasses import dataclass
from time import monotonic

from .business_plan import MAX_STEPS

MAX_TASKS = MAX_STEPS + 5
TASK_TYPES = {"metric", "catalog", "pdf", "summary"}


class TaskGraphError(ValueError):
    pass


@dataclass(frozen=True)
class Task:
    task_id: str
    task_type: str
    parameters: dict
    depends_on: tuple[str, ...] = ()


def ordered_tasks(tasks):
    if not 1 <= len(tasks) <= MAX_TASKS:
        raise TaskGraphError("task_limit")
    by_id = {}
    for task in tasks:
        if not task.task_id or task.task_id in by_id:
            raise TaskGraphError("invalid_or_duplicate_task_id")
        if task.task_type not in TASK_TYPES:
            raise TaskGraphError("unsupported_task_type")
        by_id[task.task_id] = task
    for task in tasks:
        if not set(task.depends_on) <= by_id.keys():
            raise TaskGraphError("missing_dependency")
    ordered, visited = [], set()
    while len(ordered) < len(tasks):
        ready = next((task for task in tasks if task.task_id not in visited
                      and all(dep in visited for dep in task.depends_on)), None)
        if ready is None:
            raise TaskGraphError("dependency_cycle")
        ordered.append(ready)
        visited.add(ready.task_id)
    return ordered


def execute_tasks(tasks, handlers, *, attempt_id, deadline, clock=monotonic):
    ordered = ordered_tasks(tasks)
    if any(task.task_type not in handlers for task in ordered):
        raise TaskGraphError("missing_task_handler")
    outcomes = {}
    for position, task in enumerate(ordered, 1):
        failed = [dep for dep in task.depends_on if outcomes[dep][0]["status"] != "ok"]
        if failed:
            event = {"status": "skipped", "error_code": "dependency_failed",
                     "failed_dependencies": failed, "elapsed_ms": 0}
            payload = None
        elif clock() >= deadline:
            event = {"status": "skipped", "error_code": "plan_timeout", "elapsed_ms": 0}
            payload = None
        elif task.task_type == "summary":
            event, payload = handlers["summary"](task.parameters, [outcomes[dep] for dep in task.depends_on])
        else:
            event, payload = handlers[task.task_type](task.parameters)
        outcomes[task.task_id] = ({
            **event, "task_id": task.task_id, "kind": task.task_type, "attempt_id": attempt_id,
            "depends_on": list(task.depends_on), "schedule_position": position,
        }, payload)
    return outcomes
