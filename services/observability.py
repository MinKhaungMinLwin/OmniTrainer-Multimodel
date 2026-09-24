import hashlib
import json
from contextlib import contextmanager
from typing import Any, Iterator

from openinference.instrumentation import TraceConfig, using_attributes
from openinference.instrumentation.google_genai import GoogleGenAIInstrumentor
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from phoenix.otel import register

_provider: TracerProvider | None = None


def hash_identifier(value: str | None) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def configure_tracing(settings: Any, service_name: str) -> TracerProvider | None:
    global _provider
    if not settings.tracing_enabled or not settings.tracing_endpoint:
        return None
    if _provider is not None:
        return _provider
    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": settings.environment,
        }
    )
    _provider = register(
        endpoint=settings.tracing_endpoint,
        project_name=settings.tracing_project_name,
        api_key=settings.tracing_api_key,
        protocol="http/protobuf",
        batch=True,
        verbose=False,
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.tracing_sample_ratio)),
    )
    hide_content = not settings.tracing_capture_content
    GoogleGenAIInstrumentor().instrument(
        tracer_provider=_provider,
        config=TraceConfig(
            hide_inputs=hide_content,
            hide_outputs=hide_content,
            hide_input_messages=hide_content,
            hide_output_messages=hide_content,
            hide_input_text=hide_content,
            hide_output_text=hide_content,
            hide_input_images=True,
            hide_prompts=hide_content,
            hide_choices=hide_content,
        ),
    )
    return _provider


def shutdown_tracing(provider: TracerProvider | None) -> None:
    global _provider
    if provider is None:
        return
    provider.force_flush(timeout_millis=5000)
    provider.shutdown()
    _provider = None


def tool_argument_summary(arguments: dict[str, Any]) -> str:
    return json.dumps(
        {
            "fields": sorted(arguments),
            "null_fields": sorted(name for name, value in arguments.items() if value is None),
        },
        separators=(",", ":"),
    )


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x")


@contextmanager
def traced_span(
    name: str,
    kind: OpenInferenceSpanKindValues,
    *,
    session_id: str = "",
    user_id: str = "",
    metadata: dict[str, Any] | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[trace.Span]:
    safe_metadata = metadata or {}
    with using_attributes(
        session_id=session_id,
        user_id=hash_identifier(user_id),
        metadata=safe_metadata,
    ):
        tracer = trace.get_tracer("omni-model")
        with tracer.start_as_current_span(name) as span:
            span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, kind.value)
            for key, value in (attributes or {}).items():
                if value is not None:
                    span.set_attribute(key, value)
            yield span
