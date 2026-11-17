"""Review only the public pending inventory; leave source and seal immutable."""
import argparse
from collections import Counter
from pathlib import Path

from dotenv import dotenv_values

from dataquery_agent.metric_query_service import MetricQueryService, RequestError

from .adapters import snapshot
from .dataset import DATASET, ROOT, digest
from .expansion import MANIFEST, PUBLIC, metric_request, read, sql_label, verify, write
from .review_pdf_evidence import REVIEWER, SOURCE, collect_pdf, require

VERSION = "benchmark-golden-evidence-review-v1"
REPORT = ROOT / "reports/benchmark/golden_evidence_review_v1.json"

# Decision and missing proof were independently reviewed against the public questions.
PENDING = {
    "ambiguous-credit": ("business_definition", "缺少冲销的正式映射和事件口径", "人工确认冲销是会计分录、退款还是调整"),
    "different-axes": ("business_definition", "支付方式与品类属于不同事实维度，逐行对应无依据", "确认接受独立报表或给出批准的归属规则"),
    "customer-overlap": ("business_definition", "ODS 存在订单渠道与账户键，但春季边界与渠道码需确认", "确认窗口和电话 / 网站映射后求账户集合交集"),
    "rates-delta": ("business_definition", "账户国家与订单国家口径未确认，且取消占比未开放国家轴", "声明国家来源、窗口、分母和空分母处理"),
    "keep-filter": ("conversation_fixture", "缺首轮成功请求及持久化的渠道过滤", "保存首轮请求与回执，核验新筛选是否叠加"),
    "negate": ("conversation_fixture", "缺首轮分组与明确清除操作的回执", "验证清除渠道分组后仍保留窗口"),
    "replace-axis": ("conversation_fixture", "缺国家轴已保存的会话快照", "验证替换为会员等级并保留窗口和指标"),
    "replace-clock": ("conversation_fixture", "缺首轮退货事件窗口状态及改用购买批次的约束", "确认是否澄清购买批次和观察截止"),
    "mixed-add": ("conversation_fixture", "缺首轮商品分组与结果，并列计划继承未验证", "确认补指标与既有窗口、商品轴和结果上限"),
    "follow-ref": ("conversation_fixture", "缺先前具体引用的有效版本及 Owner 绑定", "用已保存的引用快照复核同文档追问"),
    "branch-time": ("conversation_fixture", "缺两窗口比较的历史计划", "验证前者只替换基期并重新检查窗口"),
    "clarify-scope": ("conversation_fixture", "缺澄清关联回执，测试报表未唯一标识", "提供有权限且当前发布的报表与主题"),
    "confirm-share": ("conversation_fixture", "缺澄清前后的明确窗口和全渠道分母", "复核过滤参数没有误缩小分母"),
    "invalid-carry": ("conversation_fixture", "缺原支付方式分组的会话状态", "确认改指标时是否主动澄清或拒绝不兼容分组"),
    "pdf-missing-table": ("pdf_source", "授权样例没有相关表格，无法核对单元格语义", "提供有表格、行列关系可复核的授权原件"),
    "pdf-shared-filename": ("pdf_source", "已验证跨 Owner 隔离，尚缺不同 Owner 的同名双文件对照", "审核并发布双份同名试件再验证各自引用"),
    "pdf-gross": ("pdf_rule", "授权 PDF 未含折扣披露规定", "待批准的披露规则；SQL 成功而证据缺失时仅可返回 partial"),
    "pdf-attribution": ("pdf_rule", "授权 PDF 未定义账户与自然人关系", "待审核的身份规则；账户数只能独立陈述"),
    "pdf-cohort": ("pdf_rule", "授权 PDF 未定义窗口内重复下单", "核对 PDF 窗口、账户粒度和计数分母；不等同于正式复购率"),
    "pdf-distinct": ("pdf_rule", "授权 PDF 未解释商品 ID 与 SKU", "核对原文和商品身份关系；禁止用 SKU 改写正式去重规则"),
}

PDF_NEGATIVES = {
    "pdf-metadata": ("metadata_not_authority", "OCR 置信度仅是提取质量信息，不能作为正式答案依据"),
    "pdf-negative": ("no_page_evidence", "第三页无审核文本和有效 Chunk，不得返回该页引用"),
    "pdf-old-ocr": ("superseded_source", "当前版可引用第 2 页审核文本；旧转录只可在授权历史中审计"),
    "pdf-original-bbox": ("coordinates_unavailable", "修订引用可定位实际第 2 页和审核文本第 1–2 行，不能提供修订字的原图坐标"),
}

HYBRID_SQL = {
    "pdf-gross": ("item_gross_sales_amount", "2025-04-01", "2025-05-01"),
    "pdf-attribution": ("ordering_customer_count", "2025-05-01", "2025-06-01"),
    "pdf-cohort": ("customer_repeat_order_rate", "2025-08-01", "2025-09-01"),
    "pdf-distinct": ("ordered_product_count", "2025-07-01", "2025-08-01"),
}

NEGATIVES = {
    "path-funnel": ("missing_browse_fact", "ODS 无浏览事件，不能生成端到端漏斗", [], ["browse_conversion_rate"]),
    "department-cost": ("missing_cost_and_attribution", "无历史成本、部门或退款责任人，不能估计或分摊毛利",
                        [], ["historical_gross_margin_amount"]),
    "cohort-drift": ("unsupported_observation_policy", "正式明细退货率采用统一观察截止，7、30 天的逐明细窗口尚无正式口径",
                     ["item_return_rate"], []),
}


def assemble(report):
    public, manifest, old = read(PUBLIC), read(MANIFEST), read(DATASET)
    require(report["dataset_version"] == VERSION, "wrong review release")
    require(report["parent_digest"] == digest(public) and report["holdout_manifest_digest"] == digest(manifest),
            "parent data or seal changed")
    cases = public["cases"]
    pending_ids = {row["case_id"] for row in cases if row["review_status"] == "needs_review"}
    decisions = {decision["case_id"]: decision for decision in report["reviews"]}
    require(set(decisions) == pending_ids and len(report["reviews"]) == len(pending_ids), "incomplete review inventory")
    revised = []
    for row in cases:
        decision = decisions.get(row["case_id"])
        if decision is None:
            revised.append(row)
            continue
        require(decision["decision"] in {"reviewed_negative", "needs_review"}, "unknown review decision")
        reviewed = decision["decision"] == "reviewed_negative"
        label = decision["golden_update"]
        require(bool(label) == reviewed, "review state and scoring labels disagree")
        updated = row | {"review": row["review"] | {"evidence_review_version": VERSION, "decision": decision["decision"],
                                                   "reason": decision["reason"], "reviewer": REVIEWER}}
        if reviewed:
            updated.update(review_status="reviewed", golden=row["golden"] | label,
                           scoring=row["scoring"] | {"source": VERSION, "citation_support": "manual"})
        revised.append(updated)
    result = public | {"dataset_version": VERSION, "cases": revised}
    verify(result | {"dataset_version": public["dataset_version"]}, manifest, old)
    return result


def collect():
    from evaluate_business_planning import reference_connection

    public = read(PUBLIC)
    service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query").get("METRIC_QUERY_DSN"))
    unsupported_capabilities = read(ROOT / "warehouse/capability_contract.json")["unsupported"]
    pdf = collect_pdf()
    with reference_connection() as pg:
        mark = snapshot(pg)
        columns = pg.execute("""SELECT table_name, array_agg(column_name::text ORDER BY ordinal_position) AS columns
                               FROM information_schema.columns WHERE table_schema='ods' GROUP BY table_name
                               ORDER BY table_name""").fetchall()
        require({row["table_name"] for row in columns} == set(mark["tables"]), "unexpected ODS inventory")
        names = {name for row in columns for name in row["columns"]}
        require(not names.intersection({"browse_at", "department_id", "historical_unit_cost", "refund_owner_id"}),
                "negative source assumptions changed")
        sql = {key: sql_label(pg, service, [metric_request(mid, "", start, end)], mark)
               for key, (mid, start, end) in HYBRID_SQL.items()}
        try:
            service.validate(metric_request("item_return_rate", "", "2025-01-01", "2025-02-01")
                             | {"observation_cutoff": "2025-02-08"})
        except RequestError as error:
            require(error.code == "unsupported_cutoff", "return observation contract changed")
        else:
            raise ValueError("unexpected alternate cutoff")
    with reference_connection() as pg:
        require(snapshot(pg) == mark, "ODS changed since the truth queries")
    reviews = []
    for row in public["cases"]:
        if row["review_status"] != "needs_review":
            continue
        key = row["case_id"].rsplit(":", 1)[-1]
        evidence, label = {}, None
        if key in PDF_NEGATIVES:
            code, reason = PDF_NEGATIVES[key]
            active = pdf["active_citations_at_verification"][1]
            chunks = [active["chunk_id"]] if key in {"pdf-original-bbox", "pdf-old-ocr"} else []
            evidence = {"source": SOURCE, "content_hash": pdf["original_sha256"], "version": pdf["verified_version"],
                        "page": 3 if key == "pdf-negative" else 2, "relevant_chunks": chunks,
                        "expected_behavior": reason, "fixture": "ephemeral_replay",
                        "forbidden_chunk_ids": [pdf["superseded_citation"]["chunk_id"]] if key == "pdf-old-ocr" else []}
            label = {"intent": "knowledge", "tool_path": "rag", "evidence": evidence,
                     "behavior": {"review_rule": code, "criterion": reason}}
            category, followup = "document_boundary", "按同一已授权夹具重建后复核，映射本次运行的 Chunk ID"
        elif key in NEGATIVES:
            code, reason, ids, unavailable = NEGATIVES[key]
            statuses = {mid: service.metrics[mid]["definition_status"] if mid in service.metrics else
                        "unsupported" if mid in unsupported_capabilities else "unknown" for mid in ids + unavailable}
            require(all(statuses[mid] == "unsupported" for mid in unavailable), "negative catalog status changed")
            evidence = {"catalog_status": statuses, "snapshot": mark["id"],
                        "scope": "ODS inventory and Metric Query Service contract"}
            label = {"intent": "business_plan", "metric_ids": ids,
                     "behavior": {"review_rule": code, "criterion": reason,
                                  "acceptable_status": ["unsupported", "clarify"], "numerical_result_allowed": False}}
            category, followup = "capability_boundary", "缺少必要输入或正式规则时保留为能力负例"
        else:
            category, reason, followup = PENDING[key]
            if key in sql:
                evidence = {"sql": sql[key], "pdf_support": "not_confirmed", "join_claim": "not_supported"}
        reviews.append({"case_id": row["case_id"], "decision": "reviewed_negative" if label else "needs_review",
                        "reason_category": category, "reason": reason, "further_review": followup,
                        "authorization": "repo synthetic fixture or read-only public SQL Shed ODS" if evidence else
                        "no case-specific authoritative source confirmed",
                        "human_signoff": None, "evidence": evidence, "golden_update": label})

    report = {"dataset_version": VERSION, "reviewer": REVIEWER, "parent_digest": digest(public),
              "holdout_manifest_digest": digest(read(MANIFEST)), "gemini_calls": 0, "ods_snapshot": mark,
              "ods_columns": columns, "pdf": pdf, "reviews": reviews}
    updated = assemble(report)
    promoted = sum(row["decision"] == "reviewed_negative" for row in reviews)
    reviewed_before = len(read(MANIFEST)["cases"]) + sum(row["review_status"] == "reviewed" for row in public["cases"])
    report["counts"] = {"reviewed_before": reviewed_before, "pending_before": len(reviews),
                        "reviewed_after": reviewed_before + promoted,
                        "pending_after": sum(row["review_status"] == "needs_review" for row in updated["cases"]),
                        "promoted_negative": promoted, "hybrid_sql_only": len(sql)}
    report["pending_reasons"] = dict(Counter(row["reason_category"] for row in reviews if not row["golden_update"]))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", type=Path, help="Write fresh local evidence to this path")
    parser.add_argument("--export", type=Path, help="Write the reviewed public Golden dataset")
    args = parser.parse_args()
    if args.collect:
        if args.collect.exists():
            parser.error("choose a new output path for each evidence run")
        write(args.collect, collect())
    else:
        result = assemble(read(REPORT))
        if args.export:
            write(args.export, result)
        print(f"{VERSION}: {len(result['cases'])} public cases verified")


if __name__ == "__main__":
    main()
