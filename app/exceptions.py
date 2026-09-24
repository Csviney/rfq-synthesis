"""Shared exceptions for email ingestion failures."""


class UnsupportedAttachmentError(ValueError):
    """Raised for an attachment this baseline cannot read: an unrecognized
    type, one that decodes but carries no extractable text (e.g. a scanned
    PDF), or one email_parser.py can't even decode to bytes (e.g. a
    forwarded message/rfc822). Unsupported/unreadable attachments fail
    explicitly rather than being silently skipped — this can also reject an
    unrelated inline logo, which is an accepted, documented tradeoff (see
    architecture/DATA_FLOW.md)."""


class NoUsableSourceTextError(ValueError):
    """Raised when parsing succeeds but leaves nothing to extract from: an
    empty body and no attachments. Sibling of UnsupportedAttachmentError —
    architecture/LLM_DESIGN.md's failure table maps both to the same 422."""


class LlmConfigError(RuntimeError):
    """Raised when required provider configuration is missing."""


class ProviderTimeoutError(RuntimeError):
    """The provider did not respond in time. Maps to 504."""


class ProviderUnavailableError(RuntimeError):
    """The provider could not be reached, or rejected the request at the
    transport/auth/rate-limit level. Maps to 503, alongside LlmConfigError
    for missing configuration."""


class ProviderResponseError(RuntimeError):
    """The provider responded, but refused the request, returned an
    incomplete/filtered answer, or didn't return a structured result. Maps
    to 502."""
