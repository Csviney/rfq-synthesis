"""App setup, HTTP routes, and error responses. See
architecture/ARCHITECTURE.md ("HTTP and dashboard") and
architecture/LLM_DESIGN.md ("Failures") for the exact status-code mapping.
"""

import functools
from contextlib import asynccontextmanager
from email import policy
from email.parser import BytesHeaderParser
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.email_parser import EmailParseError
from app.llm_client import (
    LlmConfig,
    LlmConfigError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    build_client,
    extract,
)
from app.models import NoUsableSourceTextError, RfqResult, UnsupportedAttachmentError
from app.rfq_service import process_email
from app.store import RfqStore

ACCEPTED_CONTENT_TYPES = {"message/rfc822", "application/octet-stream"}
# Reject an oversized input outright rather than truncating a parts list;
# see architecture/DATA_FLOW.md.
MAX_EMAIL_BYTES = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = RfqStore()
    try:
        config = LlmConfig.from_env()
        client = build_client(config)
        app.state.client = client
        # The route handler only ever calls app.state.extractor — tests
        # substitute a fake one here, per architecture/IMPLEMENTATION_PLAN.md
        # ("Substitute a fake extractor only to test application plumbing").
        app.state.extractor = functools.partial(extract, client, config.model)
    except LlmConfigError as config_error:
        # Don't crash startup over missing config; fail with 503 instead
        # (see the 503 case in LLM_DESIGN.md) — but only once input
        # validation has had a chance to reject a bad request first. This
        # extractor raises lazily, exactly where the real one would run.
        app.state.client = None

        def _unconfigured_extractor(bundle, _error=config_error):
            raise _error

        app.state.extractor = _unconfigured_extractor
    yield
    if app.state.client is not None:
        app.state.client.close()


app = FastAPI(lifespan=lifespan)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
# Jinja renders None as the literal text "None"; this filter is the one
# place "missing" is turned into a display placeholder, kept distinct from
# a real falsy value like quantity 0 or an empty string (which this filter
# leaves untouched — only `None` triggers it).
templates.env.filters["display"] = lambda value: "—" if value is None else value


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Newest first, so an operator glancing at the page sees this
    # morning's RFQs at the top without scrolling.
    rfqs = list(reversed(request.app.state.store.list()))
    return templates.TemplateResponse(request, "index.html", {"rfqs": rfqs})


def _get_subject(raw: bytes) -> str | None:
    # process_email has already proven `raw` parses; this is a second,
    # header-only read for the store's display metadata, kept separate so
    # process_email's return type stays exactly the public
    # RfqResult/NonRfqResult contract. BytesHeaderParser stops at the first
    # blank line and never walks the MIME tree — email.message_from_bytes
    # would rebuild the whole multipart structure just to read one header.
    return BytesHeaderParser(policy=policy.default).parsebytes(raw).get("Subject")


@app.post("/ingest")
async def ingest(request: Request):
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in ACCEPTED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Unsupported content type; use message/rfc822 or application/octet-stream",
        )

    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > MAX_EMAIL_BYTES:
        raise HTTPException(status_code=413, detail="Email exceeds the maximum supported size")

    # Without a (trustworthy) Content-Length — e.g. a chunked upload — the
    # only way to enforce the limit is to stop reading as soon as the
    # running total crosses it, rather than buffering the whole body first
    # and checking afterward.
    size = 0
    body_parts = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_EMAIL_BYTES:
            raise HTTPException(status_code=413, detail="Email exceeds the maximum supported size")
        body_parts.append(chunk)
    raw = b"".join(body_parts)

    try:
        result = await run_in_threadpool(process_email, raw, request.app.state.extractor)
    except EmailParseError as exc:
        raise HTTPException(status_code=400, detail="Could not read the email") from exc
    except LlmConfigError as exc:
        raise HTTPException(status_code=503, detail="LLM provider is not configured") from exc
    except (UnsupportedAttachmentError, NoUsableSourceTextError) as exc:
        raise HTTPException(
            status_code=422, detail="Unsupported attachment, or no usable source text"
        ) from exc
    except ProviderTimeoutError as exc:
        raise HTTPException(status_code=504, detail="Provider request timed out") from exc
    except ProviderUnavailableError as exc:
        raise HTTPException(status_code=503, detail="LLM provider is unavailable") from exc
    except ProviderResponseError as exc:
        raise HTTPException(status_code=502, detail="Provider returned an invalid result") from exc

    if isinstance(result, RfqResult):
        subject = await run_in_threadpool(_get_subject, raw)
        request.app.state.store.add(result, subject=subject)

    return result
