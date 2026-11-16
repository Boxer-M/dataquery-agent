"""Owner-bound review receipts for local PDF evidence."""
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat

from ..conversation_store import ConversationError, owner_hash
from ..document_ingestion import extract_pdf, read_pdf
from ..metadata_service import fingerprint
from ..metrics import ROOT
from .documents import Document, KnowledgeError, ensure_safe, split_sections, validate_document

SOURCE_TYPE = "reviewed_pdf"
REVIEW_VERSION = "pdf-review-v1"
KEY_PATH = ROOT / ".document-review-key"
TEXT_LIMIT = 100_000
JSON_LIMIT = 8 * 1024 * 1024


def read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(JSON_LIMIT + 1)
    if len(raw) > JSON_LIMIT:
        raise KnowledgeError("review_json_too_large")
    return json.loads(raw)


def reviewer_key():
    try:
        fd = os.open(KEY_PATH, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise KnowledgeError("review_key_permissions")
            key = stream.read(66).strip()
    except OSError:
        raise KnowledgeError("review_key_unavailable") from None
    if not re.fullmatch(rb"[0-9a-f]{64}", key):
        raise KnowledgeError("invalid_review_key")
    return bytes.fromhex(key.decode())


def scope_for(owner):
    try:
        return owner_hash(owner)
    except ConversationError:
        raise KnowledgeError("owner_scope_required") from None


def signature(payload, key):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hmac.digest(key, canonical, "sha256").hex()


def approve(extraction, pdf, *, owner, slug, title, basis, corrections):
    scope = scope_for(owner)
    if not isinstance(extraction, dict) or extraction.get("review_state") != "unreviewed":
        raise KnowledgeError("unreviewed_extraction_required")
    if extraction.get("owner_scope") != scope:
        raise KnowledgeError("review_owner_mismatch")
    key = reviewer_key()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug) or not 1 <= len(title.strip()) <= 150:
        raise KnowledgeError("invalid_review_source")
    if not 1 <= len(basis.strip()) <= 1000:
        raise KnowledgeError("review_basis_required")
    ensure_safe(basis)
    fresh = extract_pdf(pdf, owner=owner)
    fields = ("document_id", "owner_scope", "file_sha256", "document_version", "extraction_version")
    if any(extraction.get(field) != fresh[field] for field in fields):
        raise KnowledgeError("extraction_source_mismatch")
    page_fields = ("page", "method", "status", "text", "extractor_version")
    supplied = extraction.get("pages")
    if not isinstance(supplied, list) or not all(isinstance(row, dict) for row in supplied):
        raise KnowledgeError("invalid_extraction_pages")
    if ([tuple(row.get(field) for field in page_fields) for row in supplied]
            != [tuple(row[field] for field in page_fields) for row in fresh["pages"]]):
        raise KnowledgeError("extraction_pages_mismatch")
    if any(page["status"] not in {"ok", "no_text"} for page in fresh["pages"]):
        raise KnowledgeError("extraction_not_publishable")
    candidates = [page for page in fresh["pages"] if page["status"] == "ok" and page["text"].strip()]
    if not isinstance(corrections, dict) or corrections.keys() - {str(page["page"]) for page in candidates}:
        raise KnowledgeError("invalid_review_corrections")
    pages = []
    for page in candidates:
        text = corrections.get(str(page["page"]), page["text"])
        if not isinstance(text, str) or not text.strip() or len(text) > TEXT_LIMIT:
            raise KnowledgeError("invalid_review_text")
        pages.append({"page": page["page"], "method": page["method"], "extractor_version": page["extractor_version"],
                      "text": text, "corrected": text != page["text"],
                      "extracted_text_sha256": sha256(page["text"].encode()).hexdigest(),
                      "reviewed_text_sha256": sha256(text.encode()).hexdigest()})
    if sum(len(page["text"]) for page in pages) > TEXT_LIMIT:
        raise KnowledgeError("review_text_limit")
    payload = {field: fresh[field] for field in fields if field != "document_version"}
    payload.update(review_version=REVIEW_VERSION, review_state="reviewed", source_uri=f"reviewed-pdf:{slug}",
                   title=title.strip(), basis=basis.strip(), reviewer_key_id=sha256(key).hexdigest()[:16], pages=pages)
    build_document(payload)
    return {"review": payload, "signature": signature(payload, key)}


def build_document(payload):
    passages = tuple(part for page in payload["pages"]
                     for part in split_sections(page["text"], page=page["page"]))
    doc = Document(payload["title"], SOURCE_TYPE, payload["source_uri"], payload["file_sha256"],
                   "pdf-" + fingerprint(payload)[:48], passages)
    validate_document(doc)
    return doc


def verify_receipt(receipt, *, owner, pdf):
    scope = scope_for(owner)
    if not isinstance(receipt, dict) or not isinstance(receipt.get("review"), dict):
        raise KnowledgeError("signed_review_required")
    payload, seal = receipt["review"], receipt.get("signature")
    if (not isinstance(seal, str) or not re.fullmatch(r"[0-9a-f]{64}", seal)
            or not hmac.compare_digest(signature(payload, reviewer_key()), seal)):
        raise KnowledgeError("invalid_review_signature")
    if payload.get("owner_scope") != scope:
        raise KnowledgeError("review_owner_mismatch")
    if payload.get("review_state") != "reviewed" or payload.get("review_version") != REVIEW_VERSION:
        raise KnowledgeError("review_not_publishable")
    if not pdf or sha256(read_pdf(pdf)).hexdigest() != payload.get("file_sha256"):
        raise KnowledgeError("review_source_mismatch")
    provenance = {key: val for key, val in payload.items() if key != "pages"}
    provenance["pages"] = [{key: val for key, val in page.items() if key != "text"} for page in payload["pages"]]
    return build_document(payload), provenance
