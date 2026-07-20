"""FastAPI application: health check and entrypoint.

Модели HTTP-контракта вынесены в `app.api.schemas`, а эндпоинты в `app.api.endpoints`.
Внутренний контракт критериев — `app.parsing.schema.Criteria`, его публичное
представление для поля ``criteria`` ответа даёт ``Criteria.to_public_dict()``.
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
    app.state.memory_pool = None

    from app.config import get_settings

    settings = get_settings()
    if settings.AI_ENRICHMENT_ENABLED:
        try:
            import asyncpg

            from app.ai.memory import DATABASE_URL

            app.state.memory_pool = await asyncpg.create_pool(DATABASE_URL)
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("Не удалось инициализировать пул БД: %s", e)

    yield

    if app.state.memory_pool:
        await app.state.memory_pool.close()
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
