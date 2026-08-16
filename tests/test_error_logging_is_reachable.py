"""
Errors reach somewhere a person can be told about them.

A12 assumed errors were landing in Cloud Logging and merely going unnoticed.
They were not landing there at all. The only handler on `api_monitor` was a
RotatingFileHandler writing to LOG_DIR, which is /tmp/logs when K_SERVICE is
set — per-instance and wiped on cold start. Python's last-resort stderr handler
never fired either, because it only handles records no handler took, and the
file handler took every one.

Measured, not reasoned: capturing stdout and stderr around a logger.error()
call returned two empty strings. No alert policy could have fired on that,
which is why the TypeError in the prediction path lived in production until it
happened to be narrated on screen.
"""

import io
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app


def _emit(level: str, message: str, **kwargs) -> list:
    """
    Emit through app's handlers and return the JSON objects written to stdout.

    The StreamHandler binds sys.stdout when it is constructed, so redirecting
    sys.stdout afterwards does not capture it. The handler's own stream is
    swapped instead, which is what actually intercepts the write.
    """
    stream = io.StringIO()
    handlers = [h for h in app.logger.handlers if isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.FileHandler)]
    assert handlers, "no stdout handler: nothing this logs can reach Cloud Logging"

    originals = [h.stream for h in handlers]
    for h in handlers:
        h.stream = stream
    try:
        getattr(app.logger, level)(message, **kwargs)
    finally:
        for h, original in zip(handlers, originals):
            h.stream = original

    return [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]


def test_there_is_a_handler_that_writes_to_stdout():
    """
    Cloud Run collects stdout and stderr. A log line that reaches neither
    reaches Cloud Logging, and therefore any alert policy, never.
    """
    stdout_handlers = [
        h for h in app.logger.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    assert stdout_handlers, "the only handler writes to a file on an ephemeral disk"


def test_an_error_actually_produces_output():
    """The regression: logger.error() used to write nothing anywhere visible."""
    written = _emit("error", "something broke")
    assert written, "logger.error() produced no output on stdout"


def test_the_severity_field_is_present_and_correct():
    """
    Cloud Logging reads `severity` from the JSON payload. Without it every line
    is DEFAULT severity and a `severity>=ERROR` filter matches nothing — the
    alert policy would be permanently silent while looking configured.
    """
    assert _emit("error", "broke")[0]["severity"] == "ERROR"
    assert _emit("warning", "odd")[0]["severity"] == "WARNING"
    assert _emit("info", "fine")[0]["severity"] == "INFO"


def test_an_exception_carries_its_type_and_traceback():
    """
    "Errors are up" is not actionable. The type is what groups one repeated
    fault apart from several different ones, and the traceback is the diagnosis.
    """
    try:
        raise TypeError("unsupported operand type(s) for *: 'NoneType' and 'int'")
    except TypeError as exc:
        written = _emit("error", "prediction failed", exc_info=exc,
                        extra={"endpoint": "/api/predict"})

    record = written[0]
    assert record["error_type"] == "TypeError"
    assert "NoneType" in record["stack_trace"]
    assert record["endpoint"] == "/api/predict"
    assert record["severity"] == "ERROR"


def test_each_line_is_one_parseable_json_object():
    """Cloud Logging parses per line. A multi-line record is not structured."""
    try:
        raise ValueError("boom")
    except ValueError as exc:
        written = _emit("error", "failed", exc_info=exc)

    assert len(written) == 1
    assert isinstance(written[0], dict)


def test_the_logger_does_not_emit_a_second_unstructured_copy():
    """
    Propagating to the root logger would print every line twice — once as JSON
    and once bare — and the bare copy is what a filter on jsonPayload misses.
    """
    assert app.logger.propagate is False


def test_an_unhandled_exception_has_a_handler_that_logs_it():
    """
    Without this, Starlette turns an unhandled error into a 500 and the
    traceback goes to stderr unstructured — which is how the prediction-path
    TypeError survived unnoticed.
    """
    assert hasattr(app, "log_unhandled_error")
    handlers = getattr(app.app, "exception_handlers", {})
    assert Exception in handlers, "no catch-all exception handler is registered"


def test_the_alert_policy_targets_the_severity_that_is_now_emitted():
    """
    The policy and the logger have to agree. A filter on severity>=ERROR is
    worthless if nothing sets severity, which was the case until now.
    """
    from pathlib import Path

    policy_path = Path(__file__).resolve().parent.parent / "ops" / "error_rate_alert_policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))

    condition = policy["conditions"][0]["conditionMatchedLog"]["filter"]
    assert "severity>=ERROR" in condition
    assert "cloud_run_revision" in condition
    assert policy["enabled"] is True
