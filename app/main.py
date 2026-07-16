"""FastAPI application: health check and the POST /build-url endpoint (stub).

Здесь же живут модели HTTP-контракта `BuildUrlRequest`/`BuildUrlResponse`
(решение промпта 02: это слой API, а не парсинга; внутренний контракт критериев —
`app.parsing.schema.Criteria`, его публичное представление для поля ``criteria``
ответа даёт ``Criteria.to_public_dict()``).
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from app.api.endpoints import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл приложения: инициализация и закрытие httpx-клиента."""
    app.state.http_client = httpx.AsyncClient()
    yield
    await app.state.http_client.aclose()


app = FastAPI(
    title="picurl — pik.ru URL builder",
    description=(
        "Сервис принимает свободный текст на русском языке о желаемой квартире "
        "(например: «хочу двушку у метро, до 15 млн, с отделкой») и возвращает "
        "рабочую ссылку на pik.ru с уже применёнными фильтрами и сортировкой.\n\n"
        "Пользовательский интерфейс — эта Swagger-форма: раскройте "
        "`POST /build-url`, нажмите **Try it out**, введите текст запроса в поле "
        "`text` и нажмите **Execute** — в ответе придёт JSON со ссылкой."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


class HealthResponse(BaseModel):
    """Ответ health-проверки."""

    status: str


@app.get("/health", tags=["service"])
def health() -> HealthResponse:
    """Проверка, что сервис жив."""
    return HealthResponse(status="ok")


app.include_router(api_router)
