from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.api.knowledge_routes import router as knowledge_router
from app.api.routes import router
from app.config import get_settings
from app.errors import ApplicationError, ErrorCode, ErrorDetail, ErrorResponse
from app.knowledge.runtime import build_knowledge_runtime
from app.knowledge.worker import build_worker
from app.observability.factory import build_trace_service


@asynccontextmanager
async def _knowledge_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the knowledge layer once, before the first request.

    Failure here stops the application deliberately. Serving with an
    unexpectedly non-persistent knowledge layer is worse than not serving:
    reviewers would approve metrics that vanish and workers would learn
    different things.
    """
    settings = get_settings()
    app.state.knowledge = await build_knowledge_runtime(settings)
    # Generation runs here rather than on a request, so no user waits on a
    # model call they did not ask for.
    app.state.knowledge_worker = await build_worker(settings, app.state.knowledge)
    if app.state.knowledge_worker is not None:
        await app.state.knowledge_worker.start()
    try:
        yield
    finally:
        worker = getattr(app.state, "knowledge_worker", None)
        if worker is not None:
            await worker.stop()
        runtime = getattr(app.state, "knowledge", None)
        if runtime is not None:
            await runtime.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=_knowledge_lifespan)
    traces = build_trace_service(settings)

    @app.middleware("http")
    async def request_observability(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        span = traces.start_span(
            "http.request",
            {
                "request_id": request_id,
                "http_method": request.method,
                "http_path": request.url.path,
            },
        )
        try:
            response = await call_next(request)
            span.set_attribute("http_status_code", response.status_code)
            response.headers["X-Request-ID"] = request_id
            return response
        except BaseException as exc:
            span.record_error(exc)
            raise
        finally:
            span.end()

    @app.exception_handler(ApplicationError)
    async def application_error_handler(
        request: Request,
        exc: ApplicationError,
    ) -> JSONResponse:
        response = ErrorResponse(
            error=ErrorDetail(
                code=exc.code,
                message=exc.safe_message,
                request_id=(
                    exc.request_id or getattr(request.state, "request_id", "unknown")
                ),
                retryable=exc.retryable,
            )
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=response.model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        del exc
        return _error_response(
            status_code=422,
            code=ErrorCode.INVALID_REQUEST,
            message="The analytics request is invalid.",
            request_id=getattr(request.state, "request_id", str(uuid4())),
            retryable=False,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        del exc
        return _error_response(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="The analytics request could not be completed.",
            request_id=getattr(request.state, "request_id", str(uuid4())),
            retryable=False,
        )

    app.include_router(router)
    app.include_router(knowledge_router)
    return app


app = create_app()


def _error_response(
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    request_id: str,
    retryable: bool,
) -> JSONResponse:
    response = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=request_id,
            retryable=retryable,
        )
    )
    return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))
