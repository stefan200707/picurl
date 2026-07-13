"""FastAPI application: health check and the POST /build-url endpoint (stub).

Здесь же живут модели HTTP-контракта `BuildUrlRequest`/`BuildUrlResponse`
(решение промпта 02: это слой API, а не парсинга; внутренний контракт критериев —
`app.parsing.schema.Criteria`, его публичное представление для поля ``criteria``
ответа даёт ``Criteria.to_public_dict()``).
"""

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

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
)


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


@router.post("/build-url")
def build_url(request: BuildUrlRequest) -> BuildUrlResponse:
    """Построить ссылку на pik.ru по свободному тексту (пока заглушка)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Парсинг и построение URL ещё не реализованы (см. prompts/06–09).",
    )


app.include_router(router)
