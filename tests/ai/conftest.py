import pytest

from app.ai.client import reset_circuit_breaker


@pytest.fixture(autouse=True)
def _reset_ai_circuit_breaker():
    """Circuit breaker (app/ai/client.py) — состояние на уровне модуля, поэтому
    без сброса между тестами один тест, открывший breaker, ломал бы соседние
    (`AI_CIRCUIT_BREAKER_THRESHOLD` последовательных отказов копился бы через
    границы тестов)."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()
