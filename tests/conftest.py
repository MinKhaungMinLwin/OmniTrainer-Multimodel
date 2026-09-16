"""Deterministic configuration shared by credential-free tests."""

import os

# Application modules validate configuration during import. Unit tests use inert
# values; live provider behavior belongs to the explicitly selected integration
# test suite.
os.environ.setdefault("GEMINI_API_KEY", "unit-test-gemini-key")
os.environ.setdefault("USER_API_KEY", "unit-test-user-key")
os.environ.setdefault("DEFAULT_GOOGLE_MODEL", "gemini-2.5-flash-lite")
