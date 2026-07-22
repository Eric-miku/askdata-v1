import pytest
from fastapi import HTTPException

from askdata.api.routes import _require_admin
from askdata.core.config import settings


def test_admin_guard_is_optional_for_local_development(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "")
    assert _require_admin(None) is None


def test_admin_guard_rejects_wrong_token_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "secret-token")
    with pytest.raises(HTTPException) as error:
        _require_admin("wrong-token")
    assert error.value.status_code == 403
    assert _require_admin("secret-token") is None
