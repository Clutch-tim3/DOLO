import os
import time
import json
from datetime import datetime
try:
    from anthropic import Anthropic, APIStatusError, RateLimitError
except ImportError:
    pass # Will be handled below

from agent.cost_tracking import log_api_call

# Haiku 3 pricing: $0.25/M input, $1.25/M output (per million)
PRICE_PER_TOKEN_IN = 0.25 / 1_000_000
PRICE_PER_TOKEN_OUT = 1.25 / 1_000_000

class ClaudeRateLimitExceeded(Exception):
    pass

class ClaudeAPIError(Exception):
    pass

def call_claude_with_tracking(company_id: str, messages: list, system: str = None, tools: list = None, max_tokens: int = None) -> dict:
    """
    Core wrapper for Anthropic API.
    - Requires max_tokens to be set.
    - Handles Anthropic Rate limits (Layer 1).
    - Logs cost and token metrics to SQLite (Task 7 Monitoring).
    """
    if max_tokens is None:
        raise ValueError("max_tokens MUST be set for every single API call.")
        
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # If no key, mock response but still log
        _log_mock(company_id, max_tokens)
        return {"content": "MOCKED RESPONSE: Anthropic API Key not found.", "tool_calls": []}

    try:
        client = Anthropic(api_key=api_key)
    except NameError:
        _log_mock(company_id, max_tokens)
        return {"content": "MOCKED RESPONSE: Anthropic library not installed.", "tool_calls": []}

    kwargs = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools

    start_time = time.time()
    try:
        response = client.messages.create(**kwargs)
        
        latency = (time.time() - start_time) * 1000.0
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cost = (tokens_in * PRICE_PER_TOKEN_IN) + (tokens_out * PRICE_PER_TOKEN_OUT)
        
        log_api_call(
            company_id=company_id,
            endpoint="messages.create",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            latency_ms=latency,
            status="success",
            is_rate_limited=False
        )
        
        # Parse response
        text_content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })
                
        return {"content": text_content, "tool_calls": tool_calls}

    except RateLimitError as e:
        latency = (time.time() - start_time) * 1000.0
        log_api_call(
            company_id=company_id,
            endpoint="messages.create",
            tokens_in=0, tokens_out=0, cost_usd=0.0,
            latency_ms=latency,
            status="error: rate_limit",
            is_rate_limited=True
        )
        raise ClaudeRateLimitExceeded("Anthropic API rate limit exceeded.") from e
        
    except APIStatusError as e:
        latency = (time.time() - start_time) * 1000.0
        log_api_call(
            company_id=company_id,
            endpoint="messages.create",
            tokens_in=0, tokens_out=0, cost_usd=0.0,
            latency_ms=latency,
            status=f"error: {e.status_code}",
            is_rate_limited=False
        )
        raise ClaudeAPIError(f"Anthropic API Error: {e.message}") from e
        
    except Exception as e:
        latency = (time.time() - start_time) * 1000.0
        log_api_call(
            company_id=company_id,
            endpoint="messages.create",
            tokens_in=0, tokens_out=0, cost_usd=0.0,
            latency_ms=latency,
            status="error: unknown",
            is_rate_limited=False
        )
        raise e

def _log_mock(company_id: str, max_tokens: int):
    """Log mock API calls for testing purposes without actual API key."""
    log_api_call(
        company_id=company_id,
        endpoint="mock.messages.create",
        tokens_in=50,
        tokens_out=25,
        cost_usd=0.0,
        latency_ms=10.0,
        status="success_mock",
        is_rate_limited=False
    )
