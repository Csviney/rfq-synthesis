"""Opt-in checks that hit the real, paid provider. Per
architecture/IMPLEMENTATION_PLAN.md's "Live" testing policy: a mock
returning expected data does not prove model behavior, so these are never
faked. Skipped unless explicitly enabled with RUN_LIVE_TESTS=1 *and*
OPENAI_API_KEY/OPENAI_MODEL are set — credentials alone are not consent to
spend money on every `pytest tests/` run, e.g. in a dev environment where a
.env with real keys is already loaded.

Only the step-1 schema smoke test lives here today: it proves the
ModelEnvelope/RfqResult/NonRfqResult shapes are accepted by the provider's
structured-output support. The full extraction prompt and sample matrix
belong to a later implementation step.
"""

import os

import pytest

from app.llm_client import LlmConfig, LlmConfigError, build_client
from app.models import ModelEnvelope, NonRfqResult, RfqResult

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("RUN_LIVE_TESTS") == "1"
        and os.environ.get("OPENAI_API_KEY")
        and os.environ.get("OPENAI_MODEL")
    ),
    reason="live provider check skipped; set RUN_LIVE_TESTS=1 with OPENAI_API_KEY/OPENAI_MODEL to opt in",
)


def test_provider_accepts_model_envelope_schema():
    try:
        config = LlmConfig.from_env()
    except LlmConfigError as exc:
        pytest.skip(str(exc))

    client = build_client(config)
    try:
        completion = client.beta.chat.completions.parse(
            model=config.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Reply with a NonRfqResult: isRfq=false, confidence=1, "
                        "reason='schema smoke test'."
                    ),
                },
                {"role": "user", "content": "schema smoke test"},
            ],
            response_format=ModelEnvelope,
        )
    finally:
        client.close()

    parsed = completion.choices[0].message.parsed
    assert isinstance(parsed, ModelEnvelope)
    assert isinstance(parsed.result, (RfqResult, NonRfqResult))
