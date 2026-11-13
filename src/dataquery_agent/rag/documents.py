"""Parse source locations and keep sections, paragraphs and table rows intact."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import shutil
import subprocess

from ..conversation_context import redact
from ..metadata_service import fingerprint
from ..metrics import ROOT

PARSER_VERSION = "sections-v1"
CATALOG_PATH = "metrics/warehouse_catalog_v1.json"
REPOSITORY_SOURCES = {
    "docs/warehouse/METRICS_AND_LINEAGE.md",
    "docs/warehouse/TABLE_DESIGN.md",
    "docs/warehouse/QUERY_ASSET_GOVERNANCE.md",
}
INJECTION = re.compile(
    r"忽略.{0,12}(?:指令|规则|提示)|(?:系统|开发者)(?:提示|指令|消息)|"
    r"ignore.{0,25}(?:instruction|prompt)|(?:system|developer)\s*(?:prompt|message):|"
    r"<\s*/?\s*(?:system|developer)\b|(?:api[_ -]?key|password|secret|token)\s*[:=]",
    re.I | re.S,
)
AUTHORITIES = {"catalog": 4, "business_rule": 3, "technical": 2, "synthetic_fixture": 1}


class KnowledgeError(ValueError):
    pass


@dataclass(frozen=True)
class Passage:
    text: str
    heading_path: tuple[str, ...]
    location: str


@dataclass(frozen=True)
class Document:
    title: str
    source_type: str
    source_uri: str
    content_hash: str
    version: str
    passages: tuple[Passage, ...]
    topic: str | None = None
    effective: str | None = None
    expires: str | None = None


def ensure_safe(text):
    if redact(text) != text or INJECTION.search(text):
        raise KnowledgeError("unsafe_document_or_question")


def split_sections(text, *, page=None, size=950):
    headings, output, lines = [], [], []
    start, table_header = 1, None

    def flush(end):
        if not lines:
            return
        body = "\n".join(lines).strip()
        if len(body) > 1800:
            raise KnowledgeError("section_block_too_long")
        if body:
            position = f"page:{page};" if page is not None else ""
            context = tuple(item[1] for item in headings)
            if table_header and body.startswith("|") and not body.startswith(table_header):
                context += (table_header,)
            output.append(Passage(body, context,
                                  f"{position}lines:{start}-{end}"))
        lines.clear()

    fenced = False
    for line_no, line in enumerate(text.splitlines(), 1):
        heading = None if fenced else re.fullmatch(r"(#{1,6})\s+(.+?)\s*#*\s*", line)
        if heading:
            flush(line_no - 1)
            level = len(heading[1])
            headings[:] = [item for item in headings if item[0] < level]
            headings.append((level, heading[2]))
            table_header = None
            continue
        if not line.strip() and not fenced:
            if sum(map(len, lines)) >= size // 2:
                flush(line_no - 1)
            elif lines:
                lines.append("")
            continue
        if not fenced and line.startswith("|"):
            if lines and not lines[-1].startswith("|"):
                flush(line_no - 1)
                table_header = None
            if table_header is None:
                table_header = line
            if sum(map(len, lines)) >= size:
                flush(line_no - 1)
        elif not fenced and lines and lines[-1].startswith("|"):
            flush(line_no - 1)
            table_header = None
        if not lines:
            start = line_no
        lines.append(line)
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
    if lines:
        flush(line_no)
    return tuple(output)


def parse_file(path, *, title, source_type, source_uri, version, topic=None, effective=None, expires=None):
    from datetime import date
    from urllib.parse import urlparse

    path = Path(path)
    if source_type not in AUTHORITIES or source_type == "catalog":
        raise KnowledgeError("invalid_document_type")
    if (not title or not version or len(title) > 150 or len(version) > 64 or len(source_uri) > 500
            or not source_uri or (topic and len(topic) > 120)):
        raise KnowledgeError("invalid_document_metadata")
    if source_type == "technical" and (source_uri not in REPOSITORY_SOURCES or path.resolve() != ROOT / source_uri):
        raise KnowledgeError("unapproved_repository_source")
    if source_type == "business_rule":
        origin = urlparse(source_uri)
        if not topic or not effective or origin.scheme != "https" or not origin.hostname or origin.username:
            raise KnowledgeError("business_rule_requires_verified_source_and_dates")
    if source_type == "synthetic_fixture" and not source_uri.startswith("fixture:"):
        raise KnowledgeError("synthetic_source_required")
    if effective:
        date.fromisoformat(effective)
    if expires and (not effective or date.fromisoformat(expires) <= date.fromisoformat(effective)):
        raise KnowledgeError("invalid_effective_window")
    if path.stat().st_size > 1_000_000:
        raise KnowledgeError("document_size_limit")
    raw = path.read_bytes()
    if path.suffix.lower() == ".pdf":
        executable = shutil.which("pdftotext")
        if not executable:
            raise KnowledgeError("pdf_requires_poppler")
        result = subprocess.run([executable, "-layout", str(path.resolve()), "-"], check=False,
                                capture_output=True, timeout=15)
        if result.returncode or len(result.stdout) > 250_000:
            raise KnowledgeError("pdf_parse_failed")
        pages = result.stdout.decode("utf-8").split("\f")
        passages = tuple(chunk for i, page in enumerate(pages, 1) for chunk in split_sections(page, page=i))
    elif path.suffix.lower() in {".md", ".txt"}:
        passages = split_sections(raw.decode("utf-8-sig"))
    else:
        raise KnowledgeError("unsupported_format")
    doc = Document(title, source_type, source_uri, sha256(raw).hexdigest(), version,
                   passages, topic, effective, expires)
    validate_document(doc)
    return doc


def validate_document(doc):
    if not 1 <= len(doc.passages) <= 100:
        raise KnowledgeError("document_chunk_limit")
    ensure_safe("\n".join([doc.title, doc.source_uri, doc.version, doc.topic or ""] +
                          [" ".join(chunk.heading_path) + "\n" + chunk.text for chunk in doc.passages]))


def catalog_documents(service):
    for metric_id, metric in service.metrics.items():
        title = metric["name_zh"]
        keys = ("definition", "formula", "event_time", "grain", "deduplication", "limitations")
        body = "\n".join([title] + [metric[key] for key in keys if metric.get(key)])
        position = f"#/metrics[metric_id={metric_id}]"
        yield Document(title, "catalog", CATALOG_PATH + position,
                       fingerprint(metric), service.catalog["catalog_version"],
                       (Passage(body, (title,), position),), topic=metric_id)
