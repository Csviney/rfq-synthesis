"""Provider configuration and client construction.

The extraction prompt, the single classify-and-extract call, and response
handling are added in a later implementation step (see
architecture/IMPLEMENTATION_PLAN.md, step 3). This module only owns reading
OPENAI_API_KEY / OPENAI_MODEL from the environment and building one
configured client.
"""

import os

from openai import OpenAI

# Keep ingestion bounded rather than hanging on a slow provider; see the 504
# case in architecture/LLM_DESIGN.md's failure table.
DEFAULT_TIMEOUT_SECONDS = 30.0

REQUIRED_ENV_VARS = ("OPENAI_API_KEY", "OPENAI_MODEL")


class LlmConfigError(RuntimeError):
    """Raised when required provider configuration is missing."""


class LlmConfig:
    """Provider settings read from the environment. Never logged or
    included in error responses; see the 503 case in LLM_DESIGN.md."""

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    @classmethod
    def from_env(cls) -> "LlmConfig":
        values = {name: os.environ.get(name, "").strip() for name in REQUIRED_ENV_VARS}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise LlmConfigError(
                f"Missing required environment variable(s): {', '.join(missing)}"
            )
        return cls(api_key=values["OPENAI_API_KEY"], model=values["OPENAI_MODEL"])


def build_client(config: LlmConfig) -> OpenAI:
    """One configured client with a timeout and no automatic retries, so one
    ingestion means one provider attempt. The caller owns closing it on
    application shutdown."""
    return OpenAI(api_key=config.api_key, timeout=DEFAULT_TIMEOUT_SECONDS, max_retries=0)
