import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.schemas import BuildUrlRequest, BuildUrlResponse
from app.parsing.parser import parse
from app.pik.url_builder import build_url as pik_build_url
from app.pik.validator import validate
from app.reference.loader import clear_cache
from app.reference.refresh import run_refresh

logger = logging.getLogger(__name__)
router = APIRouter(tags=["build-url"])


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Dependency для получения http-клиента из state."""
    return request.app.state.http_client


@router.post(
    "/build-url",
    summary="Сгенерировать ссылку на pik.ru",
    description=(
        "Принимает текст на естественном языке, распознаёт параметры и формирует ссылку на pik.ru."
    ),
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
    try:
        validation = await validate(criteria, client)
        # Обработка пустой выдачи
        if validation.result_count == 0:
            warnings.append("под критерии ничего не найдено")

        if validation.warning:
            warnings.append(validation.warning)
        result_count = validation.result_count
    except Exception as e:
        logger.error("Ошибка при валидации выдачи: %s", e, exc_info=True)
        warnings.append("выдача не проверена (ошибка сервиса)")
        result_count = None

    return BuildUrlResponse(
        url=url,
        criteria=criteria.to_public_dict(),
        result_count=result_count,
        warnings=warnings,
    )


@router.post(
    "/internal/refresh-dicts",
    summary="Обновить справочники",
    description="Загружает свежие справочники из API и сбрасывает кэш приложения.",
    tags=["internal"],
)
async def refresh_dicts():
    """Скрытый эндпоинт для обновления справочников и инвалидации кэша."""
    try:
        await run_refresh()
        clear_cache()
        from app.parsing.entity_match import build_choices

        build_choices.cache_clear()
        return {"status": "ok", "message": "Справочники успешно обновлены, кэш сброшен."}
    except Exception as e:
        logger.error("Ошибка при обновлении справочников: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при обновлении справочников.") from e
