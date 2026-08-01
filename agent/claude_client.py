import os
import time

from anthropic import Anthropic, APIStatusError, RateLimitError

from agent.cost_tracking import log_api_call


class ClaudeRateLimitExceeded(Exception):
    pass


class ClaudeAPIError(Exception):
    pass


# ---------------------------------------------------------------------------
# Model selection
#
# NOTE: the previous value here, "claude-haiku-4-5-20251001", was a *valid*
# model ID (Claude Haiku 4.5) — it was not the cause of the agent failing.
# Do NOT "fix" it to claude-3-5-haiku-20241022 or claude-3-5-sonnet-20241022:
# those were retired on 2026-02-19 and now genuinely 404.
#
# Default is Claude Opus 5, which is markedly better at multi-step tool use —
# the thing this agent does. To go back to the cheaper Haiku, set:
#     CLAUDE_MODEL=claude-haiku-4-5
# in .env. Pricing below is kept in sync per model so cost_tracking stays honest.
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "claude-opus-5"

# price per single token: (input, output). Source: Anthropic list pricing per 1M tokens.
MODEL_PROFILES = {
    "claude-opus-5":    {"in": 5.00 / 1_000_000, "out": 25.00 / 1_000_000, "effort": True},
    "claude-opus-4-8":  {"in": 5.00 / 1_000_000, "out": 25.00 / 1_000_000, "effort": True},
    "claude-sonnet-5":  {"in": 3.00 / 1_000_000, "out": 15.00 / 1_000_000, "effort": True},
    "claude-haiku-4-5": {"in": 1.00 / 1_000_000, "out": 5.00 / 1_000_000,  "effort": False},
    # dated alias of Haiku 4.5 — same model, same price
    "claude-haiku-4-5-20251001": {"in": 1.00 / 1_000_000, "out": 5.00 / 1_000_000, "effort": False},
}

# Fallback if someone sets CLAUDE_MODEL to something not in the table above.
# Opus-tier rates so cost is over-estimated rather than silently under-reported.
UNKNOWN_MODEL_PROFILE = {"in": 5.00 / 1_000_000, "out": 25.00 / 1_000_000, "effort": False}


def get_model() -> str:
    return os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _profile(model: str) -> dict:
    return MODEL_PROFILES.get(model, UNKNOWN_MODEL_PROFILE)


def call_claude_with_tracking(
    company_id: str,
    messages: list,
    system: str = None,
    tools: list = None,
    max_tokens: int = None,
) -> dict:
    """
    Core wrapper for the Anthropic Messages API.

    Returns a dict:
        content      -> concatenated text blocks (str)
        tool_calls   -> [{id, name, input}, ...] parsed from tool_use blocks
        stop_reason  -> "end_turn" | "tool_use" | "max_tokens" | ...
        blocks       -> the raw SDK content blocks, ready to be appended back
                        into `messages` as an assistant turn

    `stop_reason` and `blocks` are what make the agentic tool loop in
    main_agent.process_agent_chat possible — do not drop them.
    """
    if max_tokens is None:
        raise ValueError("max_tokens MUST be set for every single API call.")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Previously this returned a fake "MOCKED RESPONSE" string, which made a
        # misconfigured deployment look like a working one. Fail loudly instead;
        # tests that want the old behaviour set ALLOW_MOCK_CLAUDE=1.
        if os.environ.get("ALLOW_MOCK_CLAUDE") == "1":
            _log_mock(company_id, max_tokens)
            return {
                "content": "MOCKED RESPONSE: ANTHROPIC_API_KEY not set (ALLOW_MOCK_CLAUDE=1).",
                "tool_calls": [],
                "stop_reason": "end_turn",
                "blocks": [],
            }
        raise ClaudeAPIError(
            "ANTHROPIC_API_KEY is not set. Add it to .env (the app loads .env via "
            "load_dotenv) or export it in the environment."
        )

    model = get_model()
    profile = _profile(model)

    client = Anthropic(api_key=api_key)

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools
    if profile["effort"]:
        # Keeps spend down on a chat-shaped workload. Haiku does not accept this
        # parameter at all, hence the per-model gate.
        kwargs["output_config"] = {"effort": os.environ.get("CLAUDE_EFFORT", "medium")}

    start_time = time.time()
    try:
        response = client.messages.create(**kwargs)

        latency = (time.time() - start_time) * 1000.0
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cost = (tokens_in * profile["in"]) + (tokens_out * profile["out"])

        log_api_call(
            company_id=company_id,
            endpoint="messages.create",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            latency_ms=latency,
            status="success",
            is_rate_limited=False,
        )

        text_content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "input": block.input})

        return {
            "content": text_content,
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
            "blocks": response.content,
        }

    except RateLimitError as e:
        _log_failure(company_id, start_time, "error: rate_limit", is_rate_limited=True)
        raise ClaudeRateLimitExceeded("Anthropic API rate limit exceeded.") from e

    except APIStatusError as e:
        _log_failure(company_id, start_time, f"error: {e.status_code}")
        raise ClaudeAPIError(f"Anthropic API Error ({e.status_code}): {e.message}") from e

    except Exception:
        _log_failure(company_id, start_time, "error: unknown")
        raise


def _log_failure(company_id: str, start_time: float, status: str, is_rate_limited: bool = False):
    log_api_call(
        company_id=company_id,
        endpoint="messages.create",
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        latency_ms=(time.time() - start_time) * 1000.0,
        status=status,
        is_rate_limited=is_rate_limited,
    )


def _log_mock(company_id: str, max_tokens: int):
    """Log mock API calls for testing purposes without an actual API key."""
    log_api_call(
        company_id=company_id,
        endpoint="mock.messages.create",
        tokens_in=50,
        tokens_out=25,
        cost_usd=0.0,
        latency_ms=10.0,
        status="success_mock",
        is_rate_limited=False,
    )
