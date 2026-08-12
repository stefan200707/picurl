from typing import Any

from pydantic import BaseModel, Field

from app.geo.yandex_maps import GeoPoint, RouteLeg, TravelMode, YandexMapConfig
from app.warnings import WarningCategory, WarningSeverity, describe


class WarningDetail(BaseModel):
    """Одно предупреждение с машинно-различимой категорией (задача Г6).

    Плоское `warnings: list[str]` неразличимо: «„Привет“: не удалось распознать»
    (корректно отброшенный шум) и «„этаж от 7“: не удалось распознать»
    (потерянный фильтр) выглядят одинаково, а ссылка во втором случае неполная,
    но рабочая на вид.
    """

    text: str = Field(description="Текст предупреждения — тот же, что в плоском warnings.")
    category: WarningCategory = Field(
        description=(
            "lost — требование распознано, но в ссылку не доехало; "
            "unknown — фрагмент не распознан, природа неизвестна; "
            "noise — корректно отброшено, фильтром не было; "
            "capped — такого фильтра у pik.ru нет; "
            "unverified — фильтр применён, но result_count его не учитывает; "
            "degraded — не применили по своей вине (ИИ/сеть/данные), повтор осмыслен; "
            "info — справка о том, как сузили выдачу."
        )
    )
    severity: WarningSeverity = Field(
        description=(
            "Производная от category: lost/unknown → error, degraded/capped → warning, "
            "unverified/noise/info → info."
        )
    )

    @classmethod
    def from_warning(cls, item: str) -> "WarningDetail":
        text, category, severity = describe(item)
        return cls(text=text, category=category, severity=severity)


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
                "finish": ["готовая"],
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
        description=(
            "Нераспознанные куски текста и ослабленные критерии. "
            "Выводится из warnings_detailed (тот же порядок, те же тексты) — "
            "источник правды один, каналы не разъезжаются."
        ),
    )
    warnings_detailed: list[WarningDetail] = Field(
        default_factory=list,
        description=(
            "Те же предупреждения с категорией и severity. Разметка внедряется "
            "порциями: неразмеченная точка отдаёт category=unknown — это "
            "нормальный промежуточный статус, а не дефект."
        ),
    )
    ai_used: bool = Field(
        default=False,
        description=(
            "Реально ли ИИ повлиял на результат (успешный вызов модели или "
            "детерминированное сужение по ориентиру). False при отключённом ИИ, "
            "неудачной попытке (см. ai_failed) и при простых запросах без "
            "гео/POI-контекста, для которых обогащать нечего."
        ),
    )
    ai_failed: bool = Field(
        default=False,
        description=(
            "Была попытка обратиться к ИИ, но она провалилась (сеть/валидация/"
            "провайдер). Не путать с ai_used=False — оно означает и «не звали», "
            "и «упало»; это поле различает эти два случая, не теряя факт ошибки."
        ),
    )
    ai_cache_hit: bool = Field(
        default=False,
        description="Взят ли результат ИИ-обогащения из локального семантического кэша.",
    )
    ai_explanation: str | None = Field(
        default=None, description="Объяснение решения ИИ (почему выбраны именно эти ЖК)."
    )
    map_config: YandexMapConfig | None = Field(
        default=None,
        description=(
            "Конфигурация и данные для автоматической подгрузки Яндекс.Карт с двумя точками, "
            "построенным маршрутом и расчётом времени в пути от ЖК до целевого места."
        ),
    )


class RouteRequest(BaseModel):
    """Запрос на построение маршрута между двумя точками."""

    origin: GeoPoint = Field(description="Точка А (старт / ЖК)")
    destination: GeoPoint = Field(description="Точка Б (финиш / назначение)")
    travel_mode: TravelMode = Field(
        default=TravelMode.PEDESTRIAN,
        description="Способ перемещения: pedestrian, driving, transit, bicycle",
    )


class RouteResponse(BaseModel):
    """Ответ с построенным маршрутом и временем в пути."""

    route: RouteLeg = Field(description="Основной построенный маршрут")
    all_modes: list[RouteLeg] = Field(
        default_factory=list,
        description="Маршруты и время в пути для всех основных способов перемещения",
    )
