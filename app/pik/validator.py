from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from app.parsing.schema import Criteria, HousingType, Rooms


class ValidationResult(BaseModel):
    """Результат валидации выдачи pik.ru."""

    result_count: int | None
    ok: bool


async def validate(criteria: Criteria, client: httpx.AsyncClient) -> ValidationResult:
    """Делает проверочный запрос к data API pik.ru и возвращает счетчик выдачи.

    Это единственный сетевой вызов в рантайме. Если сеть недоступна, возвращает
    result_count=None, не роняя сервис (best-effort).
    """
    params: dict[str, str] = {}

    # 1. Комнатность
    if criteria.rooms:
        room_ids = {
            Rooms.STUDIO: "-1",
            Rooms.ONE: "1",
            Rooms.TWO: "2",
            Rooms.THREE_PLUS: "3",
        }
        params["rooms"] = ",".join(room_ids[r] for r in criteria.rooms)

    # 2. Отделка и заселение
    if criteria.finish is True:
        params["finish"] = "1"
    if criteria.ready is True:
        params["ready"] = "1"

    # 3. Локации (для API всегда в query параметрах, в отличие от URL)
    if criteria.counties:
        ids = [c.id for c in criteria.counties if c.id]
        if ids:
            params["districtCounties"] = ",".join(ids)
    if criteria.metro:
        ids = [m.id for m in criteria.metro if m.id]
        if ids:
            params["metroStations"] = ",".join(ids)
    if criteria.districts:
        ids = [d.id for d in criteria.districts if d.id]
        if ids:
            params["districtLocations"] = ",".join(ids)
    if criteria.complexes:
        ids = [c.id for c in criteria.complexes if c.id]
        if ids:
            params["blocks"] = ",".join(ids)

    # 4. Цена
    if criteria.price_min is not None:
        params["priceFrom"] = str(criteria.price_min)
    elif criteria.price_max is not None:
        params["priceFrom"] = "0"

    if criteria.price_max is not None:
        params["priceTo"] = str(criteria.price_max)

    # 5. Площадь
    if criteria.area_min is not None:
        params["areaFrom"] = str(criteria.area_min)
    if criteria.area_max is not None:
        params["areaTo"] = str(criteria.area_max)
    if criteria.area_kitchen_min is not None:
        params["areaKitchenFrom"] = str(criteria.area_kitchen_min)
    if criteria.area_kitchen_max is not None:
        params["areaKitchenTo"] = str(criteria.area_kitchen_max)

    # 6. Этаж
    if criteria.floor_min is not None:
        params["floorFrom"] = str(criteria.floor_min)
    if criteria.floor_max is not None:
        params["floorTo"] = str(criteria.floor_max)
    if criteria.not_first_floor:
        params["notFirstFloor"] = "1"
    if criteria.last_floor:
        params["lastFloor"] = "1"

    # 7. Время
    if criteria.time_on_foot is not None:
        params["timeOnFoot"] = str(criteria.time_on_foot)
    if criteria.time_on_transport is not None:
        params["timeOnTransport"] = str(criteria.time_on_transport)

    # 8. Год и месяц сдачи
    if criteria.settlement_year_from is not None:
        params["settlementYearFrom"] = str(criteria.settlement_year_from)
    if criteria.settlement_year_to is not None:
        params["settlementYearTo"] = str(criteria.settlement_year_to)
    if criteria.settlement_month_from is not None:
        params["settlementMonthFrom"] = str(criteria.settlement_month_from)
    if criteria.settlement_month_to is not None:
        params["settlementMonthTo"] = str(criteria.settlement_month_to)

    # 9. Программы и опции
    if criteria.current_benefit:
        params["currentBenefit"] = criteria.current_benefit
    if criteria.option_groups:
        params["optionGroups"] = ",".join(criteria.option_groups)
    if criteria.options:
        params["options"] = ",".join(criteria.options)

    # 10. Тип и статус
    if criteria.housing_type == HousingType.FLATS_ONLY:
        params["type"] = "1"
    if criteria.only_available:
        params["status"] = "free"

    # Сортировка не влияет на количество результатов (count),
    # поэтому её можно не передавать в backend-API,
    # но передадим для полной аутентичности если нужно.
    if criteria.sort is not None:
        params["sortBy"] = criteria.sort.field
        params["orderBy"] = criteria.sort.order

    url = f"https://api.pik.ru/v2/filter?{urlencode(params)}"

    try:
        response = await client.get(url, timeout=5.0)
        response.raise_for_status()
        data = response.json()
        count = data.get("count", 0)
        return ValidationResult(result_count=count, ok=count > 0)
    except httpx.RequestError:
        return ValidationResult(result_count=None, ok=True)
    except httpx.HTTPStatusError:
        return ValidationResult(result_count=None, ok=True)
    except (ValueError, TypeError, KeyError):
        return ValidationResult(result_count=None, ok=True)
