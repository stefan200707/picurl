"""FastAPI application: health check and the POST /build-url endpoint (stub)."""

from fastapi import APIRouter, FastAPI, HTTPException, status
from pydantic import BaseModel

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

    text: str


class BuildUrlResponse(BaseModel):
    """Ответ: готовая ссылка на pik.ru и предупреждения о нераспознанном."""

    url: str
    warnings: list[str] = []


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
