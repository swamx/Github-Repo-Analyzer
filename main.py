import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.routes import router
from app.api.judge_routes import router as judge_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def _configure_otel() -> None:
    """Configure OpenTelemetry SDK with console exporters by default.

    Set OTEL_EXPORTER_OTLP_ENDPOINT to export to a collector instead.
    Exporters are only added when OTEL_ENABLED=true (or OTLP endpoint is set)
    so that local runs stay quiet by default.
    """
    try:
        from opentelemetry import metrics as otel_metrics
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import (
            ConsoleMetricExporter,
            PeriodicExportingMetricReader,
        )
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        otel_enabled = os.getenv("OTEL_ENABLED", "false").lower() == "true"

        if not otel_enabled and not otlp_endpoint:
            logger.info("OTEL disabled — set OTEL_ENABLED=true or OTEL_EXPORTER_OTLP_ENDPOINT to enable")
            return

        # Traces
        trace_provider = TracerProvider()
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                trace_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
                logger.info("OTEL traces → OTLP %s", otlp_endpoint)
            except ImportError:
                logger.warning("opentelemetry-exporter-otlp not installed; falling back to console trace exporter")
                trace_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        else:
            trace_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            logger.info("OTEL traces → console")

        otel_trace.set_tracer_provider(trace_provider)

        # Metrics
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
                reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=otlp_endpoint), export_interval_millis=60_000)
                logger.info("OTEL metrics → OTLP %s", otlp_endpoint)
            except ImportError:
                reader = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=60_000)
                logger.warning("opentelemetry-exporter-otlp not installed; falling back to console metric exporter")
        else:
            reader = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=60_000)
            logger.info("OTEL metrics → console (60s interval)")

        otel_metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
        logger.info("OpenTelemetry SDK configured")

    except Exception as e:
        logger.warning("Failed to configure OpenTelemetry: %s — continuing without observability", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.API_TITLE, settings.API_VERSION)
    try:
        settings.validate()
        logger.info("Configuration validated")
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        raise
    _configure_otel()
    yield
    logger.info("Shutting down %s", settings.API_TITLE)


app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description=settings.API_DESCRIPTION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # cannot be True with allow_origins=["*"] — browsers reject it
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api", tags=["Analytics & Chat"])
app.include_router(judge_router, prefix="/api/judge", tags=["LLM Judge"])


@app.get("/", tags=["System"])
async def root():
    return {
        "status": "healthy",
        "service": settings.API_TITLE,
        "version": settings.API_VERSION,
        "endpoints": {
            "analyze": "/api/analyze",
            "chat": "/api/chat",
            "health": "/api/health",
            "docs": "/docs",
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
