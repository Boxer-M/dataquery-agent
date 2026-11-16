"""Extract a local PDF to an unreviewed, owner-scoped JSON manifest."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import signal
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory

from .conversation_store import ConversationError, owner_hash

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 40
MAX_PAGE_TEXT = 50_000
MAX_OUTPUT = 8 * 1024 * 1024
VERSION = "pdf-extraction-v1"
FORBIDDEN_KEYS = {"/AA", "/OpenAction", "/JavaScript", "/JS", "/EF", "/AF", "/EmbeddedFiles",
                  "/XFA", "/RichMediaContent", "/RichMediaSettings"}
FORBIDDEN_NAMES = {"/JavaScript", "/Launch", "/EmbeddedFile", "/FileAttachment", "/RichMedia", "/Movie", "/Sound"}


class ExtractionError(ValueError):
    pass


def check_pdf(reader):
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

    if reader.is_encrypted:
        raise ExtractionError("encrypted_pdf")
    seen = set()
    pending = [reader.trailer["/Root"]]
    objects = 0
    while pending:
        value = pending.pop()
        objects += 1
        if objects > 100_000:
            raise ExtractionError("pdf_structure_limit")
        if isinstance(value, IndirectObject):
            key = (value.idnum, value.generation)
            if key not in seen:
                seen.add(key)
                pending.append(value.get_object())
        elif isinstance(value, DictionaryObject):
            if FORBIDDEN_KEYS.intersection(value):
                raise ExtractionError("active_or_embedded_pdf_content")
            pending.extend(value.values())
        elif isinstance(value, ArrayObject):
            pending.extend(value)
        elif isinstance(value, NameObject) and value in FORBIDDEN_NAMES:
            raise ExtractionError("active_or_embedded_pdf_content")


def page_row(number, method, text, version, *, status="ok", error=None, blocks=None):
    return dict(page=number, method=method, text=text, extractor_version=version, status=status,
                error=error, blocks=blocks or [])


def ocr_pages(path, candidates):
    if sys.platform != "darwin":
        return {}, None, "ocr_requires_macos"
    script = Path(__file__).with_name("pdf_vision.swift")
    try:
        result = subprocess.run(["/usr/bin/swift", str(script), str(path), *map(str, candidates)],
                                check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        bundle = json.loads(result.stdout)
        return {item["page"]: item for item in bundle["pages"]}, bundle["extractor_version"], None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return {}, None, "ocr_unavailable"


def extract_pages(path):
    from pypdf import PdfReader, __version__

    with path.open("rb") as source:
        reader = PdfReader(source, strict=True)
        check_pdf(reader)
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            raise ExtractionError("pdf_page_limit")
        version = f"pypdf {__version__}"
        pages, candidates = [], {}
        for number, page in enumerate(reader.pages, 1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception:
                text = ""
                error = "native_extraction_failed"
            else:
                error = None
            if len(text.encode("utf-8")) > MAX_PAGE_TEXT:
                pages.append(page_row(number, "native_text", "", version, status="error", error="page_output_limit"))
            elif sum(c.isalnum() for c in text) >= 12:
                pages.append(page_row(number, "native_text", text, version))
            else:
                candidates[number] = (text, error)
    if candidates:
        recognized, ocr_version, ocr_error = ocr_pages(path, candidates)
        for number, (native_text, native_error) in candidates.items():
            item = recognized.get(number, {})
            blocks = item.get("blocks", [])
            error = ocr_error or item.get("error") or native_error
            if blocks:
                pages.append(page_row(number, "ocr", "\n".join(block["text"] for block in blocks),
                                      ocr_version, blocks=blocks))
            elif native_text:
                pages.append(page_row(number, "native_text", native_text, version,
                                      status="partial" if error else "ok", error=error))
            else:
                pages.append(page_row(number, "ocr", "", ocr_version, status="error" if error else "no_text",
                                      error=error))
    return sorted(pages, key=lambda row: row["page"])


def worker(path):
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    try:
        result = {"pages": extract_pages(Path(path))}
    except ModuleNotFoundError as error:
        result = {"error": "install_requirements_pdf" if error.name == "pypdf" else "pdf_parse_failed"}
    except ExtractionError as error:
        result = {"error": str(error)}
    except Exception:
        result = {"error": "pdf_parse_failed"}
    payload = json.dumps(result, ensure_ascii=False).encode()
    sys.stdout.buffer.write(payload if len(payload) <= MAX_OUTPUT else b'{"error":"document_output_limit"}')


def read_pdf(path):
    if Path(path).suffix.lower() != ".pdf":
        raise ExtractionError("pdf_extension_required")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ExtractionError("regular_file_required")
        if info.st_size > MAX_BYTES:
            raise ExtractionError("pdf_size_limit")
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ExtractionError("pdf_size_limit")
    if not raw.startswith(b"%PDF-"):
        raise ExtractionError("invalid_pdf_header")
    return raw


def extract_pdf(path, *, owner):
    try:
        scope = owner_hash(owner)
    except ConversationError:
        raise ExtractionError("owner_scope_required") from None
    raw = read_pdf(path)
    digest = sha256(raw).hexdigest()
    with TemporaryDirectory(prefix="dataquery-pdf-") as directory:
        snapshot = Path(directory) / "input.pdf"
        snapshot.write_bytes(raw)
        env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "HOME") if key in os.environ}
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        with (Path(directory) / "result.json").open("w+b") as output:
            process = subprocess.Popen(
                [sys.executable, "-c", "import sys; from dataquery_agent.document_ingestion import worker; worker(sys.argv[1])",
                 str(snapshot)],
                env=env, stdout=output, stderr=subprocess.DEVNULL, start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=55)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise ExtractionError("pdf_processing_timeout") from None
            if exit_code:
                raise ExtractionError("pdf_processing_failed")
            output.seek(0)
            try:
                result = json.load(output)
            except ValueError:
                raise ExtractionError("pdf_processing_failed") from None
    if error := result.get("error"):
        raise ExtractionError(error)
    return dict(document_id=sha256(f"{VERSION}:{scope}:{digest}".encode()).hexdigest(),
                owner_scope=scope, file_sha256=digest, document_version=digest, extraction_version=VERSION,
                review_state="unreviewed", ocr_bounds_space="normalized_rotated_crop_box_lower_left", **result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        result = extract_pdf(args.path, owner=os.getenv("DATAQUERY_DOCUMENT_OWNER"))
    except (ExtractionError, OSError) as error:
        detail = str(error) if isinstance(error, ExtractionError) else "local_file_unavailable"
        print(json.dumps({"error": detail}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if any(page["status"] in {"error", "partial"} for page in result["pages"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
