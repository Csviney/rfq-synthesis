"""CSV/PDF text extraction and SourceBundle assembly. See
architecture/DATA_FLOW.md ("Deterministic parsing").
"""

import csv
import io

from pypdf import PdfReader

from app.models import (
    EmailAttachment,
    ParsedEmail,
    SourceBundle,
    SourceChunk,
    UnsupportedAttachmentError,
)

_SUPPORTED_CSV_TYPES = {"text/csv", "application/csv", "application/vnd.ms-excel"}
_SUPPORTED_PDF_TYPES = {"application/pdf"}


def build_source_bundle(parsed: ParsedEmail) -> SourceBundle:
    chunks = [
        SourceChunk(name="email headers", text=_format_headers(parsed)),
        SourceChunk(name="email body", text=parsed.body_text),
    ]
    for attachment in parsed.attachments:
        chunks.extend(_extract_attachment_chunks(attachment))

    return SourceBundle(chunks=chunks, warnings=list(parsed.warnings))


def _format_headers(parsed: ParsedEmail) -> str:
    lines = [
        f"From: {parsed.sender or '(unknown)'}",
        f"Subject: {parsed.subject or '(none)'}",
        f"Date: {parsed.date.isoformat() if parsed.date else '(unknown)'}",
    ]
    return "\n".join(lines)


def _extract_attachment_chunks(attachment: EmailAttachment) -> list[SourceChunk]:
    content_type = attachment.content_type.lower()
    filename = attachment.filename.lower()

    if content_type in _SUPPORTED_CSV_TYPES or filename.endswith(".csv"):
        return [_extract_csv(attachment)]
    if content_type in _SUPPORTED_PDF_TYPES or filename.endswith(".pdf"):
        return _extract_pdf(attachment)

    raise UnsupportedAttachmentError(
        f"Unsupported attachment type {attachment.content_type!r} ({attachment.filename})"
    )


def _extract_csv(attachment: EmailAttachment) -> SourceChunk:
    try:
        text = attachment.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedAttachmentError(
            f"Could not decode CSV attachment {attachment.filename!r} as UTF-8"
        ) from exc

    # Round-trip through csv.reader/writer rather than passing the raw
    # bytes through: this keeps quoted commas/newlines unambiguous while
    # still preserving every header, row, empty cell, and value verbatim.
    # strict=True rejects malformed quoting (e.g. an unterminated quote)
    # instead of silently folding two rows into one multiline cell.
    try:
        rows = list(csv.reader(io.StringIO(text), strict=True))
    except csv.Error as exc:
        raise UnsupportedAttachmentError(
            f"Malformed CSV attachment {attachment.filename!r}: {exc}"
        ) from exc

    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return SourceChunk(name=f"attachment: {attachment.filename} (CSV)", text=buffer.getvalue())


def _extract_pdf(attachment: EmailAttachment) -> list[SourceChunk]:
    try:
        reader = PdfReader(io.BytesIO(attachment.content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise UnsupportedAttachmentError(
            f"Could not read PDF attachment {attachment.filename!r}"
        ) from exc

    if not any(page.strip() for page in pages):
        raise UnsupportedAttachmentError(
            f"PDF attachment {attachment.filename!r} has no extractable text "
            "(scanned or image-only PDFs are unsupported)"
        )

    return [
        SourceChunk(name=f"attachment: {attachment.filename} page {index + 1}", text=page_text)
        for index, page_text in enumerate(pages)
    ]
