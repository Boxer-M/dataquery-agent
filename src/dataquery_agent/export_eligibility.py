"""One export gate for authorized history, public responses and artifact creation."""
from .xlsx_report import generate_metric_xlsx


def xlsx_export(record):
    """Return public eligibility and optional bytes; caller must check source access first."""
    request = record.get("resolved_request") or {}
    if request.get("group_by"):
        code, message = "query_export_grouped", "当前 Excel 导出仅支持汇总结果，暂不支持分组明细"
    elif record.get("execution_status") != "ok":
        code, message = "query_export_unavailable", "此查询尚未完成或不符合当前导出格式。"
    else:
        snapshot = record.get("export_snapshot")
        if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
            code, message = "query_export_snapshot_missing", "此查询缺少导出快照，请重新运行查询后再导出"
        else:
            try:
                content = generate_metric_xlsx(snapshot["result"], snapshot["metrics"])
                return {"eligible": True, "code": "xlsx_export_available", "message": "支持导出"}, content
            except (ValueError, KeyError, TypeError):
                code, message = "query_export_unavailable", "此查询不符合当前 Excel 格式或精度要求。"
    return {"eligible": False, "code": code, "message": message}, None
