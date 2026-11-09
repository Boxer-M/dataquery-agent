"""Read-only audit for the Olist Brazilian e-commerce dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


SOURCE_URL = "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce"
SOURCE_API_URL = (
    "https://www.kaggle.com/api/v1/datasets/view/olistbr/brazilian-ecommerce"
)
ARCHIVE_NAME = "olist_brazilian_ecommerce_kaggle.zip"


@dataclass(frozen=True)
class FileAuditConfig:
    candidate_keys: tuple[tuple[str, ...], ...] = ()
    date_fields: tuple[str, ...] = ()
    money_fields: tuple[str, ...] = ()
    tracked_fields: tuple[str, ...] = ()
    tracked_pairs: tuple[tuple[str, str], ...] = ()
    status_field: str | None = None


@dataclass
class CandidateKeyTracker:
    columns: tuple[str, ...]
    seen: set[tuple[str, ...]] = field(default_factory=set)
    duplicate_rows: int = 0
    rows_with_null_key: int = 0

    def update(self, row: dict[str, str]) -> None:
        values = tuple(normalize(row.get(column)) for column in self.columns)
        if any(not value for value in values):
            self.rows_with_null_key += 1
        elif values in self.seen:
            self.duplicate_rows += 1
        else:
            self.seen.add(values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "columns": list(self.columns),
            "distinct_non_null_keys": len(self.seen),
            "duplicate_rows": self.duplicate_rows,
            "rows_with_null_key": self.rows_with_null_key,
        }


@dataclass
class DateFieldTracker:
    null_count: int = 0
    parsed_count: int = 0
    parse_error_count: int = 0
    minimum: datetime | None = None
    maximum: datetime | None = None

    def update(self, raw_value: str | None) -> None:
        value = normalize(raw_value)
        if not value:
            self.null_count += 1
            return
        try:
            parsed = parse_date(value)
        except ValueError:
            self.parse_error_count += 1
            return
        self.parsed_count += 1
        self.minimum = parsed if self.minimum is None else min(self.minimum, parsed)
        self.maximum = parsed if self.maximum is None else max(self.maximum, parsed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "null_count": self.null_count,
            "parsed_count": self.parsed_count,
            "parse_error_count": self.parse_error_count,
            "minimum": format_datetime(self.minimum),
            "maximum": format_datetime(self.maximum),
        }


@dataclass
class MoneyFieldTracker:
    null_count: int = 0
    parsed_count: int = 0
    parse_error_count: int = 0
    negative_count: int = 0
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    def update(self, raw_value: str | None) -> None:
        value = normalize(raw_value)
        if not value:
            self.null_count += 1
            return
        try:
            parsed = Decimal(value)
            if not parsed.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            self.parse_error_count += 1
            return
        self.parsed_count += 1
        if parsed < 0:
            self.negative_count += 1
        self.minimum = parsed if self.minimum is None else min(self.minimum, parsed)
        self.maximum = parsed if self.maximum is None else max(self.maximum, parsed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "null_count": self.null_count,
            "parsed_count": self.parsed_count,
            "parse_error_count": self.parse_error_count,
            "negative_count": self.negative_count,
            "minimum": format_decimal(self.minimum),
            "maximum": format_decimal(self.maximum),
        }


@dataclass
class FileAuditRuntime:
    value_counts: dict[str, Counter[str]]
    pair_values: dict[str, set[tuple[str, str]]]


FILE_CONFIGS: dict[str, FileAuditConfig] = {
    "olist_customers_dataset.csv": FileAuditConfig(
        candidate_keys=(("customer_id",), ("customer_unique_id",)),
        tracked_fields=("customer_id", "customer_unique_id"),
        tracked_pairs=(("customer_id", "customer_unique_id"),),
    ),
    "olist_geolocation_dataset.csv": FileAuditConfig(
        tracked_fields=("geolocation_zip_code_prefix",),
    ),
    "olist_order_items_dataset.csv": FileAuditConfig(
        candidate_keys=(("order_id", "order_item_id"),),
        date_fields=("shipping_limit_date",),
        money_fields=("price", "freight_value"),
        tracked_fields=("order_id", "product_id", "seller_id"),
    ),
    "olist_order_payments_dataset.csv": FileAuditConfig(
        candidate_keys=(("order_id", "payment_sequential"),),
        money_fields=("payment_value",),
        tracked_fields=("order_id",),
    ),
    "olist_order_reviews_dataset.csv": FileAuditConfig(
        candidate_keys=(("review_id",), ("review_id", "order_id")),
        date_fields=("review_creation_date", "review_answer_timestamp"),
        tracked_fields=("order_id",),
    ),
    "olist_orders_dataset.csv": FileAuditConfig(
        candidate_keys=(("order_id",),),
        date_fields=(
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
        tracked_fields=("order_id", "customer_id"),
        status_field="order_status",
    ),
    "olist_products_dataset.csv": FileAuditConfig(
        candidate_keys=(("product_id",),),
        tracked_fields=("product_id",),
    ),
    "olist_sellers_dataset.csv": FileAuditConfig(
        candidate_keys=(("seller_id",),),
        tracked_fields=("seller_id",),
    ),
    "product_category_name_translation.csv": FileAuditConfig(
        candidate_keys=(("product_category_name",),),
    ),
}


RELATIONSHIPS = (
    (
        "orders.customer_id -> customers.customer_id",
        "olist_orders_dataset.csv",
        "customer_id",
        "olist_customers_dataset.csv",
        "customer_id",
    ),
    (
        "order_items.order_id -> orders.order_id",
        "olist_order_items_dataset.csv",
        "order_id",
        "olist_orders_dataset.csv",
        "order_id",
    ),
    (
        "order_items.product_id -> products.product_id",
        "olist_order_items_dataset.csv",
        "product_id",
        "olist_products_dataset.csv",
        "product_id",
    ),
    (
        "order_items.seller_id -> sellers.seller_id",
        "olist_order_items_dataset.csv",
        "seller_id",
        "olist_sellers_dataset.csv",
        "seller_id",
    ),
    (
        "payments.order_id -> orders.order_id",
        "olist_order_payments_dataset.csv",
        "order_id",
        "olist_orders_dataset.csv",
        "order_id",
    ),
    (
        "reviews.order_id -> orders.order_id",
        "olist_order_reviews_dataset.csv",
        "order_id",
        "olist_orders_dataset.csv",
        "order_id",
    ),
)


def normalize(value: str | None) -> str:
    return "" if value is None else value.strip()


def parse_date(value: str) -> datetime:
    for date_format in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue
    raise ValueError(f"Unsupported date value: {value}")


def format_datetime(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ") if value else None


def format_decimal(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_csv(
    path: Path, config: FileAuditConfig
) -> tuple[dict[str, Any], FileAuditRuntime]:
    with path.open(encoding="utf-8-sig", newline="") as file_handle:
        reader = csv.DictReader(file_handle)
        field_names = reader.fieldnames or []
        configured_fields = {
            field_name
            for key in config.candidate_keys
            for field_name in key
        }
        configured_fields.update(config.date_fields)
        configured_fields.update(config.money_fields)
        configured_fields.update(config.tracked_fields)
        configured_fields.update(
            field_name for pair in config.tracked_pairs for field_name in pair
        )
        if config.status_field:
            configured_fields.add(config.status_field)
        missing_fields = sorted(configured_fields.difference(field_names))
        if missing_fields:
            raise ValueError(f"{path.name} is missing configured fields: {missing_fields}")

        row_count = 0
        null_counts = Counter({field_name: 0 for field_name in field_names})
        key_trackers = [CandidateKeyTracker(columns) for columns in config.candidate_keys]
        date_trackers = {
            field_name: DateFieldTracker() for field_name in config.date_fields
        }
        money_trackers = {
            field_name: MoneyFieldTracker() for field_name in config.money_fields
        }
        value_counts = {
            field_name: Counter() for field_name in config.tracked_fields
        }
        pair_values = {
            "+".join(pair): set() for pair in config.tracked_pairs
        }
        status_counts: Counter[str] = Counter()

        for row in reader:
            row_count += 1
            for field_name in field_names:
                if not normalize(row.get(field_name)):
                    null_counts[field_name] += 1
            for tracker in key_trackers:
                tracker.update(row)
            for field_name, tracker in date_trackers.items():
                tracker.update(row.get(field_name))
            for field_name, tracker in money_trackers.items():
                tracker.update(row.get(field_name))
            for field_name, counter in value_counts.items():
                value = normalize(row.get(field_name))
                if value:
                    counter[value] += 1
            for pair in config.tracked_pairs:
                values = tuple(normalize(row.get(field_name)) for field_name in pair)
                if all(values):
                    pair_values["+".join(pair)].add(values)
            if config.status_field:
                status_counts[normalize(row.get(config.status_field)) or "<NULL>"] += 1

    report = {
        "file_name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "fields": field_names,
        "row_count": row_count,
        "null_counts": dict(null_counts),
        "candidate_keys": {
            "+".join(tracker.columns): tracker.as_dict() for tracker in key_trackers
        },
        "date_fields": {
            field_name: tracker.as_dict()
            for field_name, tracker in date_trackers.items()
        },
        "money_fields": {
            field_name: tracker.as_dict()
            for field_name, tracker in money_trackers.items()
        },
    }
    if config.status_field:
        report["status_distribution"] = dict(sorted(status_counts.items()))
    return report, FileAuditRuntime(value_counts=value_counts, pair_values=pair_values)


def audit_relationships(
    runtimes: dict[str, FileAuditRuntime]
) -> list[dict[str, Any]]:
    results = []
    for name, child_file, child_field, parent_file, parent_field in RELATIONSHIPS:
        child_counts = runtimes[child_file].value_counts[child_field]
        parent_keys = set(runtimes[parent_file].value_counts[parent_field])
        orphan_keys = sorted(set(child_counts).difference(parent_keys))
        results.append(
            {
                "relationship": name,
                "child_non_null_rows": sum(child_counts.values()),
                "child_distinct_keys": len(child_counts),
                "parent_distinct_keys": len(parent_keys),
                "orphan_row_count": sum(child_counts[key] for key in orphan_keys),
                "orphan_distinct_key_count": len(orphan_keys),
                "orphan_key_samples": orphan_keys[:10],
            }
        )
    return results


def analyze_customer_identifiers(runtime: FileAuditRuntime) -> dict[str, Any]:
    pairs = runtime.pair_values["customer_id+customer_unique_id"]
    unique_to_customer: dict[str, set[str]] = defaultdict(set)
    customer_to_unique: dict[str, set[str]] = defaultdict(set)
    for customer_id, customer_unique_id in pairs:
        unique_to_customer[customer_unique_id].add(customer_id)
        customer_to_unique[customer_id].add(customer_unique_id)
    return {
        "customer_id_distinct_count": len(customer_to_unique),
        "customer_unique_id_distinct_count": len(unique_to_customer),
        "unique_ids_linked_to_multiple_customer_ids": sum(
            len(customer_ids) > 1 for customer_ids in unique_to_customer.values()
        ),
        "maximum_customer_ids_per_unique_id": max(
            (len(customer_ids) for customer_ids in unique_to_customer.values()),
            default=0,
        ),
        "customer_ids_linked_to_multiple_unique_ids": sum(
            len(unique_ids) > 1 for unique_ids in customer_to_unique.values()
        ),
        "interpretation": (
            "customer_id identifies a customer record used by an order; "
            "customer_unique_id links repeat purchases by the same logical customer."
        ),
    }


def analyze_order_cardinality(
    order_ids: set[str], child_counts: Counter[str]
) -> dict[str, Any]:
    histogram = Counter(child_counts.get(order_id, 0) for order_id in order_ids)
    total_records = sum(count * order_total for count, order_total in histogram.items())
    average = (
        Decimal(total_records) / Decimal(len(order_ids)) if order_ids else Decimal(0)
    )
    return {
        "order_count": len(order_ids),
        "orders_with_zero_records": histogram[0],
        "orders_with_one_record": histogram[1],
        "orders_with_multiple_records": sum(
            order_total for count, order_total in histogram.items() if count > 1
        ),
        "minimum_records_per_order": min(histogram, default=0),
        "maximum_records_per_order": max(histogram, default=0),
        "average_records_per_order": format(average.quantize(Decimal("0.000001")), "f"),
        "record_count_histogram": {
            str(count): order_total for count, order_total in sorted(histogram.items())
        },
    }


def analyze_geolocation(runtime: FileAuditRuntime) -> dict[str, Any]:
    prefix_counts = runtime.value_counts["geolocation_zip_code_prefix"]
    total_rows = sum(prefix_counts.values())
    return {
        "non_null_row_count": total_rows,
        "distinct_prefix_count": len(prefix_counts),
        "duplicate_rows_by_prefix": total_rows - len(prefix_counts),
        "prefixes_with_multiple_rows": sum(count > 1 for count in prefix_counts.values()),
        "maximum_rows_for_one_prefix": max(prefix_counts.values(), default=0),
        "is_unique": total_rows == len(prefix_counts),
    }


def analyze_field_capabilities(
    file_reports: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    qualified_fields = [
        f"{file_name}.{field_name}"
        for file_name, report in file_reports.items()
        for field_name in report["fields"]
    ]
    refund_amount_fields = [
        field_name
        for field_name in qualified_fields
        if "refund" in field_name.lower()
        and any(token in field_name.lower() for token in ("amount", "value"))
    ]
    refund_completion_fields = [
        field_name
        for field_name in qualified_fields
        if "refund" in field_name.lower()
        and any(
            token in field_name.lower()
            for token in ("completed", "date", "time", "timestamp")
        )
    ]
    payment_time_fields = [
        field_name
        for field_name in qualified_fields
        if "payment" in field_name.lower()
        and any(token in field_name.lower() for token in ("date", "time", "timestamp"))
    ]
    return {
        "refund_amount": {
            "available": bool(refund_amount_fields),
            "matching_fields": refund_amount_fields,
        },
        "refund_completion_time": {
            "available": bool(refund_completion_fields),
            "matching_fields": refund_completion_fields,
        },
        "payment_time": {
            "available": bool(payment_time_fields),
            "matching_fields": payment_time_fields,
        },
        "related_but_not_equivalent_fields": [
            "olist_orders_dataset.csv.order_approved_at",
            "olist_orders_dataset.csv.order_status",
            "olist_order_payments_dataset.csv.payment_value",
        ],
        "interpretation": (
            "Order approval, cancellation status, and payment value do not prove "
            "a completed refund or provide a payment event timestamp."
        ),
    }


def build_audit(raw_dir: Path) -> dict[str, Any]:
    missing_files = [
        file_name for file_name in FILE_CONFIGS if not (raw_dir / file_name).is_file()
    ]
    archive_path = raw_dir / ARCHIVE_NAME
    if not archive_path.is_file():
        missing_files.append(ARCHIVE_NAME)
    if missing_files:
        raise FileNotFoundError(f"Missing raw dataset files: {sorted(missing_files)}")

    file_reports: dict[str, dict[str, Any]] = {}
    runtimes: dict[str, FileAuditRuntime] = {}
    for file_name, config in FILE_CONFIGS.items():
        report, runtime = audit_csv(raw_dir / file_name, config)
        file_reports[file_name] = report
        runtimes[file_name] = runtime

    order_ids = set(
        runtimes["olist_orders_dataset.csv"].value_counts["order_id"]
    )
    order_cardinality = {
        "items": analyze_order_cardinality(
            order_ids,
            runtimes["olist_order_items_dataset.csv"].value_counts["order_id"],
        ),
        "payments": analyze_order_cardinality(
            order_ids,
            runtimes["olist_order_payments_dataset.csv"].value_counts["order_id"],
        ),
        "reviews": analyze_order_cardinality(
            order_ids,
            runtimes["olist_order_reviews_dataset.csv"].value_counts["order_id"],
        ),
    }
    archive_modified = datetime.fromtimestamp(
        archive_path.stat().st_mtime, tz=timezone.utc
    ).astimezone()
    return {
        "report_schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "dataset_title": "Brazilian E-Commerce Public Dataset by Olist",
            "publisher": "Olist",
            "owner_ref": "olistbr",
            "official_page": SOURCE_URL,
            "official_metadata_api": SOURCE_API_URL,
            "license": "CC BY-NC-SA 4.0",
            "kaggle_current_version_number": 2,
            "kaggle_last_updated": "2021-10-01T19:08:27.97Z",
            "kaggle_version_note": "Data Update 2021/10/01",
            "kaggle_reported_total_bytes": 126186995,
            "version_metadata_note": (
                "The official API reports currentVersionNumber=2 for the 2021 update "
                "while its versions array also contains older records numbered through 7; "
                "this report preserves the current API value without inferring a new version."
            ),
            "downloaded_at": archive_modified.isoformat(),
            "archive_file": ARCHIVE_NAME,
            "archive_size_bytes": archive_path.stat().st_size,
            "archive_sha256": sha256_file(archive_path),
        },
        "files": file_reports,
        "relationships": audit_relationships(runtimes),
        "semantic_checks": {
            "customer_identifiers": analyze_customer_identifiers(
                runtimes["olist_customers_dataset.csv"]
            ),
            "order_child_cardinality": order_cardinality,
            "geolocation_zip_prefix": analyze_geolocation(
                runtimes["olist_geolocation_dataset.csv"]
            ),
            "refund_and_payment_fields": analyze_field_capabilities(file_reports),
        },
    }


def write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data/raw"),
        help="Directory containing the untouched Olist archive and CSV files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/d1_data_audit.json"),
        help="Path for the machine-readable JSON report.",
    )
    arguments = parser.parse_args()
    report = build_audit(arguments.raw_dir)
    write_report(report, arguments.output)
    print(f"Audited {len(report['files'])} files.")
    print(f"Report written to {arguments.output}.")


if __name__ == "__main__":
    main()
