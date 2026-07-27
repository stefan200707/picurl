import logging
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from app.parsing.schema import Criteria
from app.pik.location_fallback import combine_with_fallback, resolve_fallback_block_ids

#: ЛОКАЦИОННЫЕ query-параметры, которые ``api.pik.ru/v2/filter`` принимает, но
#: РЕАЛЬНО ИГНОРИРУЕТ при подсчёте count (аудит 2026-07-23, живые замеры curl:
#: baseline без параметров / запрос с настоящим GUID метро / запрос с заведомо
#: фейковым GUID (``deadbeef-...``) / запрос с несуществующим id округа —
#: все четыре вернули ПОБАЙТОВО одинаковый count. Единственный локационный
#: параметр, который бэкенд реально проверяет — ``blocks``
#: (``blocks=999999`` -> count=0, подтверждено).
UNVERIFIED_LOCATION_PARAMS: tuple[str, ...] = (
    "metroStations",
    "districtLocations",
    "districtCounties",
)

#: НЕлокационные параметры, которые бэкенд игнорирует так же молча (живые замеры
#: 2026-07-27): ``blocks=477&rooms=1`` → 54; ``+settlementYearFrom=2030&
#: settlementYearTo=2031`` → 54; ``+finish=0`` → 54. Контроль, что бэкенд не
#: «сломан вообще»: ``blocks=411&timeOnFoot=12`` → 0 — то есть игнорируются именно
#: эти параметры. Без них ``validate()`` выдавал за полноценную проверку число,
#: не учитывающее 2 из 6 фильтров ссылки — ровно то нарушение инварианта, ради
#: которого константа и заведена.
UNVERIFIED_NON_LOCATION_PARAMS: tuple[str, ...] = (
    "finish",
    "settlementYearFrom",
    "settlementYearTo",
)

#: Все параметры, не отражённые в ``result_count``. Если хоть один уходит в
#: запрос, соответствующий фильтр НЕ отражён в count — это не «квартиры не
#: найдены», а «мы не можем это проверить», и врать об этом молча нельзя
#: (инвариант проекта). Имя намеренно осталось прежним: константа с самого начала
#: описывала «что бэкенд не проверяет», а не только локации.
UNVERIFIED_BY_BACKEND_PARAMS: tuple[str, ...] = (
    UNVERIFIED_LOCATION_PARAMS + UNVERIFIED_NON_LOCATION_PARAMS
)


class ValidationResult(BaseModel):
    """Результат валидации выдачи pik.ru."""

    result_count: int | None
    ok: bool
    warning: str | None = None
    #: True, если среди отправленных параметров есть хотя бы один из
    #: UNVERIFIED_LOCATION_PARAMS — result_count в этом случае НЕ отражает
    #: соответствующий локационный фильтр (см. модульную константу выше).
    #: Отдельное структурное поле (в дополнение к тексту в ``warning``) — на
    #: случай, если владелец HTTP-слоя (app/api/schemas.py, вне зоны
    #: ответственности этой правки) захочет отдавать его явным полем ответа.
    location_filters_not_verified: bool = False


async def validate(criteria: Criteria, client: httpx.AsyncClient) -> ValidationResult:
    """Делает проверочный запрос к data API pik.ru и возвращает счетчик выдачи.

    Это единственный сетевой вызов в рантайме. Если сеть недоступна, возвращает
    result_count=None, не роняя сервис (best-effort).

    Не все локационные фильтры этот бэкенд проверяет одинаково честно (см.
    UNVERIFIED_BY_BACKEND_PARAMS) — в этом случае result_count всё равно
    возвращается (не None, сеть-то отработала), но ``warning``/
    ``location_filters_not_verified`` явно сообщают, что число не учитывает
    локационный фильтр, вместо того чтобы молча выдавать его за полноценную
    проверку. Отдельно: сущности без достоверного id (задача Б аудита) здесь
    заменяются geo-фолбэком на ``blocks`` (задача В) — тем же самым, что
    использует `app.pik.url_builder.build_url`, чтобы result_count проверял
    ровно то сужение, которое получит пользователь по ссылке.
    """
    params: dict[str, str] = criteria.to_query_dict()
    warning_parts: list[str] = []

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

    # 3.5 Geo-фолбэк на blocks для локаций без достоверного id — см. docstring.
    # ПЕРЕСЕЧЕНИЕ, а не объединение (Дефект №2 фикс) — та же логика, что
    # app.pik.url_builder.build_url, теперь общий хелпер
    # app.pik.location_fallback.combine_with_fallback: иначе result_count
    # проверял НЕ то сужение, которое получит пользователь по ссылке (живой
    # замер: 3889 вместо реальных 598 — validate() объединял фолбэк вместо
    # пересечения).
    fallback = resolve_fallback_block_ids(criteria)
    if fallback.block_ids:
        existing_blocks = [b for b in params.get("blocks", "").split(",") if b]
        params["blocks"] = ",".join(
            combine_with_fallback(existing_blocks, fallback, criteria, warning_parts)
        )

    # Сортировка (sortBy/orderBy) уже добавлена в params через
    # criteria.to_query_dict() выше — повторный расчёт здесь был мёртвым
    # кодом (AUDIT_REPORT 2.1).

    # Поле ответа осталось ПРО ЛОКАЦИИ (контракт API не меняется), а warning
    # честно перечисляет все непроверяемые фильтры — включая нелокационные.
    location_filters_not_verified = any(key in params for key in UNVERIFIED_LOCATION_PARAMS)
    if location_filters_not_verified:
        warning_parts.append(
            "result_count не учитывает фильтр по метро/округу/району — "
            "api.pik.ru/v2/filter не проверяет эти параметры (подтверждено "
            "живыми замерами); достоверна только часть по комнатности/цене/ЖК"
        )
    unverified_present = [key for key in UNVERIFIED_NON_LOCATION_PARAMS if key in params]
    if unverified_present:
        # Перечисляем только то, что реально ушло в запрос: «не учитывает отделку»
        # при отсутствии finish было бы такой же неправдой, как молчание.
        labels: list[str] = []
        if "finish" in unverified_present:
            labels.append("отделку")
        if any(key.startswith("settlementYear") for key in unverified_present):
            labels.append("год заселения")
        warning_parts.append(
            f"result_count не учитывает {' и '.join(labels)} — api.pik.ru/v2/filter "
            "игнорирует эти параметры (подтверждено живыми замерами)"
        )
    warning_parts.extend(fallback.notes)
    success_warning = "; ".join(warning_parts) or None

    url = f"https://api.pik.ru/v2/filter?{urlencode(params)}"

    try:
        response = await client.get(url, timeout=5.0)
        response.raise_for_status()
        data = response.json()
        count = data.get("count", 0)
        return ValidationResult(
            result_count=count,
            ok=count > 0,
            warning=success_warning,
            location_filters_not_verified=location_filters_not_verified,
        )
    except httpx.RequestError as e:
        # Сеть недоступна/таймаут — ожидаемый best-effort исход, не падение
        # сервиса: одна внятная строка без traceback (шум в логах вводил в
        # заблуждение). Контракт ответа не меняется.
        logging.warning("Валидация: сетевая ошибка (%s), выдача не проверена", type(e).__name__)
        return ValidationResult(result_count=None, ok=True, warning="выдача не проверена")
    except httpx.HTTPStatusError as e:
        # 5xx/4xx приходят со стороны api.pik.ru (их сервер), ошибка уже
        # обработана — traceback здесь только шумел. Логируем код статуса.
        logging.warning("Валидация: pik.ru вернул %s, выдача не проверена", e.response.status_code)
        return ValidationResult(result_count=None, ok=True, warning="выдача не проверена")
    except (ValueError, TypeError) as e:
        logging.warning("Ошибка парсинга ответа при валидации: %s", e, exc_info=True)
        return ValidationResult(result_count=None, ok=True, warning="выдача не проверена")
