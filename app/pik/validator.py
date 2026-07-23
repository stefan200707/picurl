import logging
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from app.parsing.schema import Criteria


class ValidationResult(BaseModel):
    """Результат валидации выдачи pik.ru."""

    result_count: int | None
    ok: bool
    warning: str | None = None


async def validate(criteria: Criteria, client: httpx.AsyncClient) -> ValidationResult:
    """Делает проверочный запрос к data API pik.ru и возвращает счетчик выдачи.

    Это единственный сетевой вызов в рантайме. Если сеть недоступна, возвращает
    result_count=None, не роняя сервис (best-effort).
    """
    params: dict[str, str] = criteria.to_query_dict()

    # 1. Комнатность
    if criteria.rooms:
        params["rooms"] = ",".join(r.id for r in criteria.rooms)

    # 2. Отделка и заселение
    if criteria.finish:
        params["finish"] = "1"
    if criteria.ready is True:
        params["ready"] = "1"

    # 3. Локации (для API всегда в query параметрах, в отличие от URL) —
    # общая сборка id (AUDIT_REPORT 2.3)
    params.update(criteria.location_query_dict())

    # Сортировка (sortBy/orderBy) уже добавлена в params через
    # criteria.to_query_dict() выше — повторный расчёт здесь был мёртвым
    # кодом (AUDIT_REPORT 2.1).

    url = f"https://api.pik.ru/v2/filter?{urlencode(params)}"

    try:
        response = await client.get(url, timeout=5.0)
        response.raise_for_status()
        data = response.json()
        count = data.get("count", 0)
        return ValidationResult(result_count=count, ok=count > 0)
    except httpx.RequestError as e:
        logging.warning("Ошибка запроса при валидации: %s", e, exc_info=True)
        return ValidationResult(result_count=None, ok=True, warning="выдача не проверена")
    except httpx.HTTPStatusError as e:
        logging.warning("Ошибка статуса при валидации: %s", e, exc_info=True)
        return ValidationResult(result_count=None, ok=True, warning="выдача не проверена")
    except (ValueError, TypeError) as e:
        logging.warning("Ошибка парсинга ответа при валидации: %s", e, exc_info=True)
        return ValidationResult(result_count=None, ok=True, warning="выдача не проверена")
