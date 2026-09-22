# Implementation plan

Build the [required workflow](SPEC.md#the-core-task-required) in this order. Add focused tests alongside each step; use completion checks to guide progress.

| Step | Work | Completion check |
|---|---|---|
| 1. Contract and model connection | Define the public models and private provider envelope from `LLM_DESIGN.md`. Configure the SDK from environment variables. | Both response shapes validate locally; a real-provider schema smoke test works or the access issue is recorded. |
| 2. Deterministic parsing | Implement email decoding, body selection, and CSV/PDF extraction into `SourceBundle`. | Original samples yield complete source text without AI; the PDF bundle retains the body-only NEO-6M addition. |
| 3. Extraction and service | Add the prompt, single model call, response validation, and short orchestration function. | A body RFQ and a non-RFQ work with the real model; failures remain errors. |
| 4. HTTP and storage | Connect `POST /ingest` to the service and save successful RFQs in one shared in-memory store. | Responses match the public contract exactly; negative results and failures do not add records. |
| 5. Dashboard | Render the store through `GET /` and one Jinja template. | A newly ingested RFQ appears after refresh with all fields and warnings; missing values and text render safely. |
| 6. Acceptance and handoff | Run the sample checks in `DATA_FLOW.md`, fix core failures, and write the README from actual behavior. | Documented setup works from a clean start; results and limitations reflect tests actually run. |

## Testing

**Offline:** test MIME/CSV/PDF parsing, contract validation, API responses, storage, and template escaping. Substitute a fake extractor only to test application plumbing. Cover an invalid input, unreadable attachment, model failure, and exact removal of the provider wrapper.

**Live:** explicitly opt in to model calls on the supported sample set. Check the sample matrix, including ambiguity, both negatives, the injected restock email, and body/PDF completeness. A mock returning expected data does not prove model behavior.

Keep `labels.json` as test reference data, never runtime answers. Compare stable fields and quantities rather than exact confidence or warning wording.

## Handoff

The README should cover setup, environment variables, an ingestion example, tests run, decisions, limitations, possible core improvements, and actual time spent as requested in [What to submit](SPEC.md#what-to-submit).

Before considering a step finished, trace one relevant sample through its changed code and explain where deterministic processing ends and model interpretation begins. Claude and Codex should work from these same documents and avoid simultaneous edits to the same files.
