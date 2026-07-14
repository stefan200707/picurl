"""FastAPI application: health check and the POST /build-url endpoint (stub).

Здесь же живут модели HTTP-контракта `BuildUrlRequest`/`BuildUrlResponse`
(решение промпта 02: это слой API, а не парсинга; внутренний контракт критериев —
`app.parsing.schema.Criteria`, его публичное представление для поля ``criteria``
ответа даёт ``Criteria.to_public_dict()``).
"""

from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.parsing.parser import parse
from app.pik.url_builder import build_url as pik_build_url
from app.pik.validator import validate


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


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Dependency для получения http-клиента из state."""
    return request.app.state.http_client


class BuildUrlRequest(BaseModel):
    """Запрос: свободный текст с пожеланиями к квартире."""

    text: str = Field(
        description=(
            "Свободный текст на русском языке с пожеланиями к квартире: "
            "комнатность, бюджет, метро/район, отделка, сортировка и т.д."
        ),
        examples=["хочу двушку у метро, до 15 млн, с отделкой"],
        min_length=1,
    )


class BuildUrlResponse(BaseModel):
    """Ответ: готовая ссылка на pik.ru и предупреждения о нераспознанном."""

    url: str = Field(description="Готовая ссылка на pik.ru с применёнными фильтрами.")
    criteria: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Распознанные критерии в человекочитаемом виде (результат Criteria.to_public_dict())."
        ),
        examples=[
            {
                "rooms": "2",
                "price_max": 15000000,
                "metro": ["Аэропорт Внуково"],
                "finish": True,
                "sort": "price_asc",
            }
        ],
    )
    result_count: int | None = Field(
        default=None,
        description=(
            "Число объектов в выдаче по данным backend-API pik.ru; "
            "None — если валидация выдачи не выполнялась/не удалась."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Нераспознанные куски текста и ослабленные критерии.",
    )


class HealthResponse(BaseModel):
    """Ответ health-проверки."""

    status: str


@app.get("/health", tags=["service"])
def health() -> HealthResponse:
    """Проверка, что сервис жив."""
    return HealthResponse(status="ok")


router = APIRouter(tags=["build-url"])


@router.post(
    "/build-url",
    summary="Сгенерировать ссылку на pik.ru",
    description="Принимает текст на естественном языке, распознаёт параметры и формирует ссылку на pik.ru.",
)
async def build_url(
    request: BuildUrlRequest,
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> BuildUrlResponse:
    """Построить ссылку на pik.ru по свободному тексту."""
    text = request.text.strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Текст запроса не может быть пустым.",
        )

    # 1. Парсинг
    parse_result = parse(text)
    criteria = parse_result.criteria
    warnings = parse_result.warnings.copy()

    # 2. Построение URL
    url = pik_build_url(criteria)

    # 3. Валидация выдачи
    validation = await validate(criteria, client)

    # Обработка пустой выдачи
    if validation.result_count == 0:
        warnings.append("под критерии ничего не найдено")
    
    if validation.warning:
        warnings.append(validation.warning)

    return BuildUrlResponse(
        url=url,
        criteria=criteria.to_public_dict(),
        result_count=validation.result_count,
        warnings=warnings,
    )


app.include_router(router)
