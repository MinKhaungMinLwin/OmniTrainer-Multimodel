from types import SimpleNamespace

import services.observability as observability


def test_trace_metadata_does_not_include_tool_values():
    summary = observability.tool_argument_summary(
        {"customer_name": "Private Customer", "phone": "+15551234567", "technician_id": None}
    )
    assert "customer_name" in summary
    assert "phone" in summary
    assert "technician_id" in summary
    assert "Private Customer" not in summary
    assert "+15551234567" not in summary
    assert observability.hash_identifier("tenant-secret") != "tenant-secret"


def test_tracing_is_disabled_without_configuration():
    settings = SimpleNamespace(tracing_enabled=False, tracing_endpoint=None)
    assert observability.configure_tracing(settings, "test-service") is None


def test_tracing_hides_model_content_by_default(monkeypatch):
    captured: dict = {}
    provider = SimpleNamespace(force_flush=lambda **_: None, shutdown=lambda: None)

    def fake_register(**kwargs):
        captured["register"] = kwargs
        return provider

    def fake_instrument(_self, **kwargs):
        captured["instrument"] = kwargs

    monkeypatch.setattr(observability, "register", fake_register)
    monkeypatch.setattr(observability.GoogleGenAIInstrumentor, "instrument", fake_instrument)
    monkeypatch.setattr(observability, "_provider", None)
    settings = SimpleNamespace(
        tracing_enabled=True,
        tracing_endpoint="http://phoenix:6006/v1/traces",
        tracing_project_name="omni-test",
        tracing_api_key="trace-secret",
        tracing_sample_ratio=1.0,
        tracing_capture_content=False,
        environment="test",
    )
    assert observability.configure_tracing(settings, "omni-test") is provider
    assert captured["register"]["batch"] is True
    assert captured["register"]["api_key"] == "trace-secret"
    config = captured["instrument"]["config"]
    assert config.hide_inputs is True
    assert config.hide_outputs is True
    assert config.hide_input_images is True
    observability.shutdown_tracing(provider)
