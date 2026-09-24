# RFQ ingestion

Turns raw emails and CSV/PDF attachments into structured RFQs, with a dashboard and an advisory next action for the quoting team.

## How to start

Requires Python 3.10+ and an OpenAI API key. From the repository directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
```

Add your key to `.env`. The default model is `gpt-4o-mini`; change `OPENAI_MODEL` if needed. Keep `.env` out of version control.

```bash
uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8000
```

In another terminal, submit all sample emails:

```bash
for sample in samples/*.eml; do
  printf '\n--- %s ---\n' "$sample"
  curl --silent --show-error --max-time 90 \
    --header 'Content-Type: message/rfc822' \
    --data-binary "@$sample" \
    --write-out '\nHTTP %{http_code}\n' \
    http://127.0.0.1:8000/ingest
done
```

These requests make paid model calls. Open or refresh [localhost:8000](http://127.0.0.1:8000) to inspect the results and filter by recommended action. Expect seven RFQs if classification and extraction succeed. The two non-RFQs return JSON but aren't stored; the image sample returns 422 because images are unsupported. Re-running adds duplicates; restarting clears the store.

Run offline tests with:

```bash
.venv/bin/python -m pytest tests/ -q
```

Live tests are opt-in and use paid calls. To load `.env` and run them:

```bash
.venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv(); import os; os.environ["RUN_LIVE_TESTS"] = "1"; import pytest; raise SystemExit(pytest.main(["tests/test_live_samples.py", "-q"]))'
```

## Decisions and tradeoffs

- **Parse before calling the model.** Python reads email, CSV, and text-PDF content; the model handles meaning and ambiguity. Images were outside the time budget. Supporting them needs OCR or a vision model, another processing path, and checks that rows, part numbers, and quantities were read correctly.
- **Preserve ambiguity within the contract.** A quantity range becomes its lower bound, with the full range kept in item notes. This avoids overstating demand, but isn't a confirmed order quantity. Missing quantities become `0`, as the spec requires, and trigger clarification rather than being treated as zero demand. Suspicious facts, such as a due date before the email date, are flagged for review instead of replaced with a guess.
- **Treat email content as untrusted.** Application instructions go in the system message; email and attachment content go in a separate user message. The system prompt explicitly tells the model to treat that content as data and ignore embedded instructions. This reduces the risk of prompt injection, but doesn't guarantee protection. The output is limited tothe validated schema and there is no authority to execute business actions so risk here is minimized. Adding an additional review model who's sole task is to find potentially infected responses could be an easy lift to increase protection.
- **Start with a small model.** I chose `gpt-4o-mini` to keep costs and response times low. Live tests showed useful extraction, but inconsistent triage. This is a starting choice, not a claim that it is the best model for the job.
- **Extract and triage in one call.** Both use the same source content, so a second call would repeat work and add cost. The tradeoff is a more complex prompt. Triage stays internal; the public response still follows the spec.
- **Bound provider calls.** A 30-second timeout and no automatic retries limit waiting and avoid silently making repeated attempts. The tradeoff is that temporary provider failures reach the caller instead of being retried.
- **Python, FastAPI, and Pydantic.** Python has the parsing and model libraries needed here. FastAPI keeps the HTTP layer small, and Pydantic checks responses before they reach storage or the caller. Valid structure doesn't guarantee correct facts.
- **Simple UI and storage.** Jinja renders the dashboard without a separate frontend build. A Python list demonstrates the full flow without a database. It loses records on restart and doesn't share data across server processes. Saving results to JSON or using SQLite would add persistence; deduplication would need a separate policy, so re-ingestion currently adds another entry.
- **Fail explicitly on unread content.** Unsupported attachments and provider failures return errors instead of silently producing partial results. This is conservative: an unrelated unsupported attachment can reject an otherwise useful email.
- **Separate software tests from model evaluation.** Offline tests cover schemas, parsing, errors, storage, and rendering using samples and constructed edge cases. Live tests check actual model behavior. Coverage is bounded, and passing the samples doesn't establish reliability on unfamiliar emails or repeated runs.

## What I would improve with more time

- Compare models on unseen RFQs and repeated runs, measuring extraction quality, consistency, latency, and cost.
- Make triage more consistent by extracting evidence for quantity ranges and sourcing requirements before choosing an action. Current recommendations can miss coordination needs or give the wrong pricing advice for ranges.
- Add human review and a correction history, including a way to catch RFQs incorrectly classified as non-RFQs. Calibrate confidence before using it to route work.
- Connect costs, inventory, customer history, and quote outcomes so priority reflects expected profit relative to quoting effort. Email urgency and order size alone don't tell us which opportunities will make money.
- Reduce duplicate warnings that describe the same issue in different words.

More detail is in [FUTURE_FIXES.md](FUTURE_FIXES.md).

**Time spent:** approximately 4.5 hours.
