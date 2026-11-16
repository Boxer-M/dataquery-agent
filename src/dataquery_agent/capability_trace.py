"""Bind a metric tool event to the capability decision for its own task."""
BINDING_FIELDS = ("plan_id", "task_id", "attempt_id", "authorization_subject", "capability_phase")


def capability_link(event, records):
    trace_id = event.get("capability_trace_id")
    if not trace_id:
        return None
    matches = [record for record in records if record.get("trace_id") == trace_id]
    if len(matches) != 1:
        return None
    route = matches[0]
    if route.get("status") != "STANDARD_METRIC":
        return None
    if any(field in event or field in route for field in BINDING_FIELDS):
        if any(field not in event or event[field] != route.get(field) for field in BINDING_FIELDS):
            return None
        if event["authorization_subject"] != event.get("request"):
            return None
    return route
