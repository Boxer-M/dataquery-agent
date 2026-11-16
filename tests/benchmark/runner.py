"""Run one benchmark mode per report. Offline fixed proposals are the default."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from . import adapters
from .dataset import DATASET, ROOT, digest, migrate, validate
from .scoring import scores, telemetry

MODES = ("deterministic", "fixed", "replay", "postgres", "gemini", "browser")


def git(*args):
    return subprocess.check_output(["git", *args], text=True, cwd=ROOT).strip()


def execute(mode, variant):
    if mode == "deterministic":
        return adapters.deterministic()
    if mode == "replay":
        return adapters.saved_replay(variant)
    if mode in {"fixed", "postgres"}:
        return adapters.fixed_or_postgres(mode)
    return adapters.live_run(mode)


def run(mode, *, archive_variant="baseline"):
    dataset = json.loads(DATASET.read_text())
    validate(dataset)
    migrated, _ = migrate()
    if dataset != migrated:
        raise ValueError("legacy dataset drift: review fixtures and regenerate")
    started = datetime.now(timezone.utc).isoformat()
    failure = None
    try:
        rows, snapshot, config = execute(mode, archive_variant)
    except Exception as error:
        # Exception text may include a DSN or provider request; only return a type.
        rows, snapshot, config = [], None, {}
        failure = type(error).__name__
    prompts = sorted({prompt for row in rows for prompt in row.get("prompt_versions", [])})
    models = sorted({model for row in rows for model in row.get("observed_models", [])})
    identity = {
        "mode": mode, "code_commit": git("rev-parse", "HEAD"),
        "code_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "dataset_version": dataset["version"], "dataset_sha256": digest(dataset),
        "case_ids_sha256": digest([row["case_id"] for row in rows]),
        "database_snapshot": snapshot, "model_config": config,
        "prompt_versions_observed": prompts, "observed_models": models,
        "python": sys.version.split()[0],
    }
    return {
        "report_version": 1, "identity": identity, "comparison_key": digest(identity),
        "started_at_utc": started, "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "number_of_cases": len(rows), "run_failure": failure, "metrics": scores(rows),
        "telemetry": telemetry(rows), "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES, default="fixed")
    parser.add_argument("--allow-live-model", action="store_true")
    parser.add_argument("--archive-variant", choices=("baseline", "specialists"), default="baseline")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--migration", action="store_true", help="Verify and summarize the frozen fixture migration")
    args = parser.parse_args()
    if args.mode in {"gemini", "browser"} and not args.allow_live_model:
        parser.error("gemini and browser require --allow-live-model (generation and embeddings)")
    if args.migration:
        dataset, report = migrate()
        if dataset != json.loads(DATASET.read_text()):
            parser.error("frozen dataset needs regeneration")
    else:
        report = run(args.mode, archive_variant=args.archive_variant)
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(payload)
    else:
        print(payload, end="")
    if args.migration:
        return 0
    success = report["metrics"]["task_success"]
    return int(report["run_failure"] is not None or not success["denominator"]
               or success["numerator"] < success["denominator"])


if __name__ == "__main__":
    raise SystemExit(main())
