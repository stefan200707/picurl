import logging
from pathlib import Path
from typing import Annotated

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.api.schemas import (
    BuildUrlRequest,
    BuildUrlResponse,
    RouteRequest,
    RouteResponse,
    WarningDetail,
)
from app.geo.yandex_maps import (
    TravelMode,
    build_map_context,
    calculate_route,
)
from app.parsing.parser import parse
from app.pik.url_builder import build_url as pik_build_url
from app.pik.validator import validate
from app.reference.loader import clear_cache
from app.reference.refresh import run_refresh

logger = logging.getLogger(__name__)
router = APIRouter(tags=["build-url"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Dependency для получения http-клиента из state."""
    client = getattr(request.app.state, "http_client", None)
    if client is None:
        return httpx.AsyncClient()
    return client


def get_memory_pool(request: Request) -> "asyncpg.Pool | None":
    """Dependency для получения пула БД из state."""
    return getattr(request.app.state, "memory_pool", None)


@router.post(
    "/build-url",
    summary="Сгенерировать ссылку на pik.ru",
    description=(
        "Принимает текст на естественном языке, распознаёт параметры, формирует ссылку на pik.ru "
        "и подготавливает данные для подгрузки Яндекс.Карт с построенным маршрутом."
    ),
)
async def build_url(
    request: BuildUrlRequest,
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    pool: Annotated["asyncpg.Pool | None", Depends(get_memory_pool)],
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

    # 1.5. ИИ-обогащение (теперь для всех запросов)
    from app.ai.enrichment import enrich, merge_enrichment

    enrichment = await enrich(
        text,
        criteria,
        warnings,
        pool=pool,
        option_candidates=parse_result.option_candidates,
    )
    criteria = merge_enrichment(criteria, enrichment)
    ai_meta = enrichment.meta

    # 2. Построение URL
    url = pik_build_url(criteria, warnings)

    # 3. Валидация выдачи
    try:
        validation = await validate(criteria, client)
        # Обработка пустой выдачи
        if validation.result_count == 0:
            warnings.append("под критерии ничего не найдено")

        # extend, а не append: валидатор отдаёт СПИСОК фактов, каждый из которых
        # должен остаться отдельным элементом ответа (один элемент — один факт).
        warnings.extend(validation.warnings)
        result_count = validation.result_count
    except Exception as e:
        logger.error("Ошибка при валидации выдачи: %s", e, exc_info=True)
        warnings.append("выдача не проверена (ошибка сервиса)")
        result_count = None

    # 4. Яндекс.Карты: подготовка двух точек, построение маршрута и времени в пути от ЖК
    map_config = await build_map_context(criteria, text, http_client=client)

    # Категории собираем ДО конструирования модели: pydantic коэрсит подкласс
    # str к обычному str, и атрибут внутри BuildUrlResponse уже не доживёт.
    # Плоский warnings выводим из detailed, чтобы источник правды был один.
    warnings_detailed = [WarningDetail.from_warning(w) for w in warnings]

    return BuildUrlResponse(
        url=url,
        criteria=criteria.to_public_dict(),
        result_count=result_count,
        warnings=[detail.text for detail in warnings_detailed],
        warnings_detailed=warnings_detailed,
        ai_used=ai_meta.ai_used,
        ai_failed=ai_meta.ai_failed,
        ai_cache_hit=ai_meta.cache_hit,
        ai_explanation=ai_meta.explanation,
        map_config=map_config,
    )


@router.post(
    "/api/geo/route",
    response_model=RouteResponse,
    summary="Построить маршрут между двумя точками",
    description="Рассчитывает маршрут, расстояние и время в пути между двумя точками.",
    tags=["geo"],
)
async def calculate_geo_route(
    request: RouteRequest,
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> RouteResponse:
    """Построить маршрут между Точкой А и Точкой Б с расчётом времени для видов транспорта."""
    main_route = await calculate_route(
        origin=request.origin,
        destination=request.destination,
        mode=request.travel_mode,
        client=client,
    )

    all_modes = []
    for mode in [
        TravelMode.PEDESTRIAN,
        TravelMode.DRIVING,
        TravelMode.TRANSIT,
        TravelMode.BICYCLE,
    ]:
        m_route = await calculate_route(
            origin=request.origin,
            destination=request.destination,
            mode=mode,
            client=client,
        )
        all_modes.append(m_route)

    return RouteResponse(route=main_route, all_modes=all_modes)


@router.get(
    "/map",
    response_class=HTMLResponse,
    summary="Интерактивная страница с поиском и Яндекс.Картами",
    description=(
        "Страница поиска с автоподгрузкой Яндекс.Карт, "
        "построением маршрута и отображением времени в пути."
    ),
    tags=["map"],
)
async def get_map_page():
    """Отдать веб-интерфейс с Яндекс.Картами."""
    from app.config import get_settings

    settings = get_settings()
    template_path = TEMPLATES_DIR / "index.html"
    if not template_path.exists():
        raise HTTPException(status_code=404, detail="Шаблон карты не найден.")

    html_content = template_path.read_text(encoding="utf-8")
    script_url = "https://api-maps.yandex.ru/2.1/?lang=ru_RU"
    if settings.YANDEX_MAPS_API_KEY:
        script_url = (
            f"https://api-maps.yandex.ru/2.1/?lang=ru_RU&apikey={settings.YANDEX_MAPS_API_KEY}"
        )
    html_content = html_content.replace("{{ script_url }}", script_url)
    return HTMLResponse(content=html_content)


@router.get(
    "/",
    response_class=HTMLResponse,
    summary="Главная страница",
    include_in_schema=False,
)
async def get_root_page():
    """Перенаправить или отобразить интерактивную карту поиска."""
    return await get_map_page()


@router.post(
    "/internal/refresh-dicts",
    summary="Обновить справочники",
    description="Загружает свежие справочники из API и сбрасывает кэш приложения.",
    tags=["internal"],
)
async def refresh_dicts(request: Request):
    """Скрытый эндпоинт для обновления справочников и инвалидации кэша."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.INTERNAL_REFRESH_TOKEN:
        raise HTTPException(
            status_code=503, detail="Токен для обновления справочников не настроен."
        )

    token = request.headers.get("X-Internal-Token")
    if token != settings.INTERNAL_REFRESH_TOKEN:
        raise HTTPException(status_code=403, detail="Неверный токен.")

    try:
        await run_refresh()
        clear_cache()
        from app.parsing.entity_match import build_choices

        build_choices.cache_clear()
        return {"status": "ok", "message": "Справочники успешно обновлены, кэш сброшен."}
    except Exception as e:
        logger.error("Ошибка при обновлении справочников: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при обновлении справочников.") from e
