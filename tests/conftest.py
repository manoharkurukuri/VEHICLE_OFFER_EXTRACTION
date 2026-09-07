"""Shared test fixtures.

Sets a dummy LLM key so services/processors can be constructed without a real
key, and points output at a temporary directory so tests never touch real
storage.
"""

import os

import pytest
from pydantic import SecretStr

from app.core.config import settings

settings.gemini_api_key = SecretStr("test-key")
os.environ.setdefault("GEMINI_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def temp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "storage"))
    yield
