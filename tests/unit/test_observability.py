import logging

from app.observability.service import LoggingTraceService


def test_logging_trace_does_not_emit_secret_exception_text() -> None:
    logger = logging.getLogger("enterprise_data_agent.telemetry")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        span = LoggingTraceService().start_span(
            "llm.generate",
            {"request_id": "request-1", "provider": "configured"},
        )
        span.record_error(RuntimeError("api_key=unit-test-secret"))
        span.end()
    finally:
        logger.removeHandler(handler)

    assert len(records) == 1
    rendered = f"{records[0].getMessage()} {getattr(records[0], 'telemetry', {})}"
    assert "unit-test-secret" not in rendered
    assert "RuntimeError" in rendered
