"""App setup, HTTP routes, and error responses."""

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
from app.exceptions import (
    LlmConfigError,
    NoUsableSourceTextError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnsupportedAttachmentError,
)
from app.llm_client import LlmConfig, build_client, extract
from app.models import TRIAGE_CATEGORY_LABELS, RfqResult, TriageCategory
from app.rfq_service import process_email
from app.store import RfqStore

ACCEPTED_CONTENT_TYPES = {"message/rfc822", "application/octet-stream"}
# Reject an oversized input outright rather than truncating a parts list.
MAX_EMAIL_BYTES = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = RfqStore()
    try:
        config = LlmConfig.from_env()
        client = build_client(config)
        app.state.client = client
        # The route handler only ever calls app.state.extractor — tests
        # substitute a fake one here instead of hitting the real model.
        app.state.extractor = functools.partial(extract, client, config.model)
    except LlmConfigError as config_error:
        # Don't crash startup over missing config; fail with 503 instead,
        # but only once input validation has had a chance to reject a bad
        # request first. This extractor raises lazily, exactly where the
        # real one would run.
        app.state.client = None

        def _unconfigured_extractor(bundle, _error=config_error):
            raise _error

        app.state.extractor = _unconfigured_extractor
    yield
    if app.state.client is not None:
        app.state.client.close()


app = FastAPI(lifespan=lifespan)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
# Jinja renders None as the literal text "None" so this explicitly converts
# missing values to "-"
templates.env.filters["display"] = lambda value: "—" if value is None else value


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, category: TriageCategory | None = None):
    # Newest first, so an operator glancing at the page sees this
    # morning's RFQs at the top without scrolling.
    all_rfqs = list(reversed(request.app.state.store.list()))
    rfqs = all_rfqs if category is None else [r for r in all_rfqs if r.triage.category == category]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "rfqs": rfqs,
            "total_count": len(all_rfqs),
            "selected_category": category,
            "categories": TRIAGE_CATEGORY_LABELS,
        },
    )


def _get_subject(raw: bytes) -> str | None:
    # process_email has already proven `raw` parses; this is a second,
    # header-only read for the store's display metadata, kept separate so
    # process_email's return type stays exactly the public
    # RfqResult/NonRfqResult contract.
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
        envelope = await run_in_threadpool(process_email, raw, request.app.state.extractor)
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

    if isinstance(envelope.result, RfqResult):
        subject = await run_in_threadpool(_get_subject, raw)
        request.app.state.store.add(envelope.result, envelope.triage, subject=subject)

    return envelope.result
