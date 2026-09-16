"""Modular FastAPI application for the Omni Model platform."""

from services.api.omni_api.main import create_app

__all__ = ["create_app"]
