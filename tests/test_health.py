"""Smoke tests: the app imports, health responds, the stub endpoint is wired up."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    """Health-роут отвечает 200 и статусом ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_build_url_stub_returns_501() -> None:
    """Заглушка POST /build-url зарегистрирована и отвечает 501."""
    response = client.post("/build-url", json={"text": "двушка у метро до 15 млн"})
    assert response.status_code == 501


def test_build_url_validates_body() -> None:
    """Тело без обязательного поля text отклоняется валидацией."""
    response = client.post("/build-url", json={})
    assert response.status_code == 422
