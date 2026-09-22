# Decisions and tradeoffs

The specification calls for a working, understandable [core and dashboard](SPEC.md#the-core-task-required), with [limited infrastructure](SPEC.md#constraints-and-non-goals). These choices serve that scope rather than a hypothetical production platform.

| Decision | Why | Alternative and accepted tradeoff |
|---|---|---|
| One Python/FastAPI application | Keeps parsing, HTTP, and the dashboard together in small modules. | A separate frontend/backend or services add boundaries that this task does not need. |
| Jinja server-rendered dashboard | Jinja fills an HTML template with Python data. One list/detail view needs no client-side state or build system. | React/Next.js offers richer interaction but adds tooling. Static HTML plus JavaScript is viable but needs fetch/render logic. Our view refreshes manually. |
| One structured classification/extraction request | Both decisions use the same source context; matches the two public result branches. | A separate classifier can save cost in some workloads, but adds a gate and a second call for RFQs. No claim that one call is universally cheapest or most accurate. |
| Deterministic parsing before the LLM | MIME, CSV, and PDF libraries expose source content; the model handles intent and ambiguity. | Sending raw payloads to a model mixes decoding with interpretation. Local PDF extraction may still lose layout. |
| Strict public models, private provider wrapper | Preserves downstream JSON while accommodating the provider's schema shape. | Loose JSON is simpler initially but easier to break. Validation cannot prove extracted facts. |
| In-memory store | Sufficient for the required local dashboard; no database is required. | File storage would survive restart. The baseline loses records on restart and allows duplicate ingestions. |
| Explicit errors for unread content | Avoids making a partial extraction look complete or classifying failure as a non-RFQ. | Continuing with warnings is more permissive. Our conservative policy can reject unrelated unsupported attachments. |
| Notes/warnings for ambiguity | Fits the fixed schema without inventing new fields. | Ranges and split deadlines cannot be represented losslessly. Our chosen conventions are documented in `DATA_FLOW.md`. |

## Later improvements, not current tasks

Broaden MIME/charset and multi-page text-PDF tests where parsing fails. Evaluate prompt revisions on held-out intent, ambiguity, and injection cases. Consider a small file-backed store only when restart persistence is needed.

These improve the existing core; they do not authorize additional product features or infrastructure. Record observed limitations in the README rather than scaffolding future subsystems.
