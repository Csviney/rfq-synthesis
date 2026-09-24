"""MIME decoding: headers, body text, and decoded attachment bytes. No AI,
and no attachment-content interpretation.
"""

import email
from email import policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from app.exceptions import UnsupportedAttachmentError
from app.models import EmailAttachment, ParsedEmail


class EmailParseError(ValueError):
    """Raised when the raw input cannot be read as an email at all."""


def parse_email(raw: bytes) -> ParsedEmail:
    if not raw or not raw.strip():
        raise EmailParseError("empty email input")

    msg = email.message_from_bytes(raw, policy=policy.default)
    if not isinstance(msg, EmailMessage):
        # policy.default always returns an EmailMessage; this guards the
        # type for callers rather than a real runtime branch.
        raise EmailParseError("unreadable email input")

    warnings: list[str] = []

    date_header = msg.get("Date")
    date = None
    if date_header:
        try:
            date = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            warnings.append(f"Could not parse Date header: {date_header!r}")

    body_text, body_warnings = _select_body(msg)
    warnings.extend(body_warnings)

    attachments = []
    for index, part in enumerate(_iter_all_attachments(msg)):
        filename = part.get_filename() or f"attachment-{index + 1}"
        content = part.get_payload(decode=True)
        if content is None:
            # e.g. a forwarded message/rfc822: its payload is a sub-message
            # object, not encoded bytes. Fail explicitly rather than
            # silently dropping it from the bundle; this baseline does not
            # support forwarded-email attachments.
            raise UnsupportedAttachmentError(
                f"Could not decode attachment {filename!r} ({part.get_content_type()})"
            )
        attachments.append(
            EmailAttachment(
                filename=filename,
                content_type=part.get_content_type(),
                content=content,
            )
        )

    return ParsedEmail(
        sender=msg.get("From"),
        subject=msg.get("Subject"),
        date=date,
        body_text=body_text,
        attachments=attachments,
        warnings=warnings,
    )


def _is_container(part: EmailMessage) -> bool:
    """True for a real multipart/* container. `is_multipart()` is also
    true for message/rfc822 (its payload is a list too, holding the one
    forwarded message), which would make a forwarded-email attachment look
    like a container to recurse into rather than a leaf to yield — and it
    would vanish instead of being reported as unsupported."""
    return part.get_content_maintype() == "multipart"


def _iter_all_attachments(part: EmailMessage):
    """`EmailMessage.iter_attachments()` only looks at *immediate*
    children, and returns nothing at all when called on a
    multipart/alternative — so a real attachment nested inside, e.g., a
    multipart/related below one alternative branch is never visited.
    Recurse into every multipart child (via iter_parts) so nested
    attachments are found; each level's own iter_attachments() call still
    correctly excludes that level's body candidate."""
    if not _is_container(part):
        return

    for child in part.iter_attachments():
        if not _is_container(child):
            yield child

    for child in part.iter_parts():
        if _is_container(child):
            yield from _iter_all_attachments(child)


def _select_body(msg: EmailMessage) -> tuple[str, list[str]]:
    """Prefer nonempty plain text; fall back to HTML (converted to text)
    only when plain text is missing or blank. Never execute HTML or fetch
    links — just strip tags. Chooses exactly one alternative."""
    plain_part = msg.get_body(preferencelist=("plain",))
    if plain_part is not None and plain_part.get_content().strip():
        return plain_part.get_content(), []

    html_part = msg.get_body(preferencelist=("html",))
    if html_part is not None:
        return _html_to_text(html_part.get_content()), ["Body was HTML-only; converted to text"]

    if plain_part is not None:
        return plain_part.get_content(), []

    return "", ["Email has no readable text body"]


class _TextExtractingHTMLParser(HTMLParser):
    _BLOCK_TAGS = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
    # Table cells get a separator, not a line break: a row's cells must
    # stay on one line (so "ABC123" and "50" don't merge into "ABC12350"
    # or get mistaken for two separate rows), while still being clearly
    # split from each other.
    _CELL_TAGS = {"td", "th"}
    _SKIP_CONTENT_TAGS = {"script", "style"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_CONTENT_TAGS:
            self._skip_depth += 1
        elif tag in self._CELL_TAGS:
            self._chunks.append("\t")
        elif tag in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_CONTENT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        text = "".join(self._chunks)
        lines = (line.strip() for line in text.splitlines())
        return "\n".join(line for line in lines if line)


def _html_to_text(html: str) -> str:
    parser = _TextExtractingHTMLParser()
    parser.feed(html)
    return parser.get_text()
