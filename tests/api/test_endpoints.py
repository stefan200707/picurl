import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_refresh_dicts_no_token_configured():
    with patch.dict(os.environ, clear=True):
        from app.config import get_settings
        get_settings.cache_clear()

        response = client.post("/internal/refresh-dicts")
        assert response.status_code == 503
        assert "не настроен" in response.json()["detail"]

def test_refresh_dicts_invalid_token():
    with patch.dict(os.environ, {"INTERNAL_REFRESH_TOKEN": "secret"}):
        from app.config import get_settings
        get_settings.cache_clear()

        response = client.post("/internal/refresh-dicts", headers={"X-Internal-Token": "wrong"})
        assert response.status_code == 403
        assert response.json()["detail"] == "Неверный токен."

@patch("app.api.endpoints.run_refresh")
@patch("app.api.endpoints.clear_cache")
def test_refresh_dicts_valid_token(mock_clear_cache, mock_run_refresh):
    with patch.dict(os.environ, {"INTERNAL_REFRESH_TOKEN": "secret"}):
        from app.config import get_settings
        get_settings.cache_clear()

        response = client.post("/internal/refresh-dicts", headers={"X-Internal-Token": "secret"})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        mock_run_refresh.assert_awaited_once()
        mock_clear_cache.assert_called_once()
