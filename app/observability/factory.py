from app.config import Settings
from app.observability.gateway import TraceService
from app.observability.service import LoggingTraceService, NoopTraceService


def build_trace_service(settings: Settings) -> TraceService:
    if settings.observability_provider == "logging":
        return LoggingTraceService()
    return NoopTraceService()
