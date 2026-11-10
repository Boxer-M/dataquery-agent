import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


SOURCE_PAGE = "https://sqlshed.com/practice-datasets/ecommerce-orders/"
BASE_URL = "https://sqlshed.com/datasets/ecommerce_orders"
FILES = {
    "categories.parquet": 24,
    "customers.parquet": 12_000,
    "products.parquet": 2_000,
    "orders.parquet": 90_000,
    "order_items.parquet": 215_000,
    "payments.parquet": 95_000,
    "returns.parquet": 12_000,
    "ecommerce_orders.schema.sql": None,
}
CHUNK_SIZE = 1024 * 1024
TIMEOUT_SECONDS = 120


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(path: Path, manifest: dict) -> None:
    temporary = path.with_suffix(".json.part")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def download_file(
    destination: Path,
    url: str,
    expected_rows: int | None,
    recorded: dict | None,
    opener=urlopen,
) -> dict:
    if destination.exists():
        if not recorded:
            raise RuntimeError(f"同名文件缺少校验记录，拒绝覆盖：{destination}")
        actual_hash = sha256_file(destination)
        if (
            actual_hash != recorded["sha256"]
            or destination.stat().st_size != recorded["size_bytes"]
        ):
            raise RuntimeError(f"现有文件与校验记录不一致：{destination}")
        return recorded

    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    request = Request(url, headers={"User-Agent": "DataQuery-Agent/1.0"})
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            with temporary.open("xb") as output:
                while chunk := response.read(CHUNK_SIZE):
                    output.write(chunk)
                    digest.update(chunk)
            length = response.headers.get('Content-Length') if hasattr(response, 'headers') else None
            if length is not None and temporary.stat().st_size != int(length):
                raise RuntimeError(f"下载长度不一致：{destination.name}")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return {
        "url": url,
        "expected_rows": expected_rows,
        "size_bytes": destination.stat().st_size,
        "sha256": digest.hexdigest(),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    raw_dir = project_root / "data" / "raw" / "sqlshed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_dir / "download_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "source_page": SOURCE_PAGE,
            "license": "CC0 1.0 Universal",
            "files": {},
        }

    for file_name, expected_rows in FILES.items():
        print(f"检查或下载 {file_name}（网络超时 {TIMEOUT_SECONDS} 秒）", flush=True)
        record = download_file(
            raw_dir / file_name,
            f"{BASE_URL}/{file_name}",
            expected_rows,
            manifest["files"].get(file_name),
        )
        manifest["files"][file_name] = record
        write_manifest(manifest_path, manifest)
        print(
            f"{file_name}: {record['size_bytes']} bytes, "
            f"sha256={record['sha256']}"
        )


if __name__ == "__main__":
    main()
