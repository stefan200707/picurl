"""Единый контракт критериев поиска — pydantic-модель `Criteria`.

Центральный контракт проекта: в `Criteria` сходятся regex-правила (промпт 04),
матчинг сущностей (промпт 05) и фасад `parse` (промпт 06); из неё `url_builder`
(промпт 07) строит URL. Набор полей соответствует фильтрам `pik.ru/search`
(источник правды — `docs/pik-url-schema.md`).

Решения по дизайну (зафиксированы здесь, чтобы последующие промпты на них
опирались):

- **Скалярные поля**: ``None`` = «не задано».
- **Списочные поля** (rooms, metro, counties, districts, complexes,
  option_groups, options): «не задано» = пустой список — downstream-коду удобнее
  итерироваться без проверок на ``None``.
- **rooms** — всегда список (в т.ч. из одного элемента): выбор «single-путь vs
  multi-query» — задача `url_builder`, он решает по длине списка. Дубликаты
  схлопываются валидатором с сохранением порядка.
- **finish** — ``bool | None`` (трёхзначный enum не нужен): URL-схема pik.ru
  поддерживает только слаг ``finish`` («готовая отделка»), т.е. ``True``.
  «Без отделки» (``False``) в URL не выражается — `url_builder` обязан отправить
  это в ``warnings`` (инвариант «ничего не отбрасывается молча»).
- **sort** — строковый ключ ``price_asc | price_desc | area_asc | area_desc``
  (как в примере ТЗ: ``"sort": "price_asc"``); properties ``field``/``order``
  дают `url_builder` значения для ``sortBy``/``orderBy``. ``None`` = сортировка
  не указана → параметры сортировки в URL не добавляются (решение вопроса №1).
- **Сущности** (метро/округа/районы/ЖК) — `MatchedEntity`: то, что вернул
  матчер (человекочитаемое имя + слаг + id/GUID); финальный маппинг в
  путь/query — задача `url_builder`.
- **Модели API** (`BuildUrlRequest`/`BuildUrlResponse`) живут в `app/main.py`
  рядом с эндпоинтом: это контракт HTTP-слоя, а не парсинга. Публичное
  представление критериев для ответа даёт :meth:`Criteria.to_public_dict`.
"""

from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Rooms(StrEnum):
    """Комнатность (чипы на pik.ru: студия / 1 / 2 / 3+)."""

    STUDIO = "studio"
    ONE = "one"
    TWO = "two"
    THREE_PLUS = "three_plus"


#: Человекочитаемые метки комнатности для публичного представления критериев.
ROOMS_LABELS: dict[Rooms, str] = {
    Rooms.STUDIO: "студия",
    Rooms.ONE: "1",
    Rooms.TWO: "2",
    Rooms.THREE_PLUS: "3+",
}


class Finish(IntEnum):
    """Отделка."""

    NONE = 0
    READY = 1
    WHITE_BOX = 2
    FURNISHED = 3


FINISH_LABELS: dict[Finish, str] = {
    Finish.NONE: "без отделки",
    Finish.READY: "готовая",
    Finish.WHITE_BOX: "предчистовая",
    Finish.FURNISHED: "с мебелью",
}


class Sort(StrEnum):
    """Сортировка выдачи: строковый ключ вида ``price_asc`` (как в примере ТЗ)."""

    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    AREA_ASC = "area_asc"
    AREA_DESC = "area_desc"

    @property
    def field(self) -> str:
        """Значение для query-параметра ``sortBy`` (``price`` | ``area``)."""
        field, _, _ = self.value.rpartition("_")
        return field

    @property
    def order(self) -> str:
        """Значение для query-параметра ``orderBy`` (``asc`` | ``desc``)."""
        _, _, order = self.value.rpartition("_")
        return order


class HousingType(StrEnum):
    """Тип жилья: только квартиры (``type=1``) или квартиры и апартаменты."""

    FLATS_ONLY = "flats_only"
    ANY = "any"


class MatchedEntity(BaseModel):
    """Сущность справочника (метро/округ/район/ЖК), найденная матчером.

    Хранит ровно то, что вернул матчер: человекочитаемое имя, слаг для пути URL
    и id/GUID для query-параметра (числовые id тоже хранятся строкой — формат
    един для GUID метро и числовых id округов/районов/ЖК).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    slug: str | None = None
    id: str | None = None


class Criteria(BaseModel):
    """Структурированные критерии поиска квартиры, извлечённые из текста.

    Все поля опциональны: «пустой» ``Criteria()`` валиден и означает
    «фильтры не заданы».
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # --- Комнатность -----------------------------------------------------
    rooms: list[Rooms] = Field(default_factory=list)

    # --- Цена, рубли ------------------------------------------------------
    price_min: int | None = Field(default=None, ge=0)
    price_max: int | None = Field(default=None, ge=0)

    # --- Площадь, м² ------------------------------------------------------
    area_min: float | None = Field(default=None, ge=0)
    area_max: float | None = Field(default=None, ge=0)
    area_kitchen_min: float | None = Field(default=None, ge=0)
    area_kitchen_max: float | None = Field(default=None, ge=0)

    # --- Этаж ---------------------------------------------------------------
    floor_min: int | None = Field(default=None, ge=1)
    floor_max: int | None = Field(default=None, ge=1)
    not_first_floor: bool = False
    last_floor: bool = False
    not_last_floor: bool = False

    # --- Отделка и заселение -------------------------------------------------
    finish: list[Finish] = Field(default_factory=list)
    ready: bool | None = None

    # --- Локации (результаты матчера, промпт 05) ---------------------------
    metro: list[MatchedEntity] = Field(default_factory=list)
    counties: list[MatchedEntity] = Field(default_factory=list)
    districts: list[MatchedEntity] = Field(default_factory=list)
    complexes: list[MatchedEntity] = Field(default_factory=list)

    # --- Время до метро, минуты ---------------------------------------------
    time_on_foot: int | None = Field(default=None, ge=1)
    time_on_transport: int | None = Field(default=None, ge=1)

    # --- Срок заселения -------------------------------------------------------
    settlement_year_from: int | None = Field(default=None, ge=2000)
    settlement_year_to: int | None = Field(default=None, ge=2000)
    settlement_month_from: int | None = Field(default=None, ge=1, le=12)
    settlement_month_to: int | None = Field(default=None, ge=1, le=12)

    # --- Сортировка и прочее ---------------------------------------------------
    sort: Sort | None = None
    housing_type: HousingType | None = None
    only_available: bool = False

    # --- Расширяемость: слаги «как есть» (см. docs/pik-url-schema.md) --------
    current_benefit: str | None = None
    option_groups: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    view: str | None = None

    @field_validator("rooms")
    @classmethod
    def _dedupe_rooms(cls, value: list[Rooms]) -> list[Rooms]:
        """Схлопнуть дубликаты комнатности, сохранив порядок упоминания."""
        return list(dict.fromkeys(value))

    @field_validator("finish")
    @classmethod
    def _dedupe_finish(cls, value: list[Finish]) -> list[Finish]:
        """Схлопнуть дубликаты отделки, сохранив порядок упоминания."""
        return list(dict.fromkeys(value))

    def to_public_dict(self) -> dict[str, Any]:
        """Публичное (человекочитаемое) представление для поля ``criteria`` ответа.

        Включает только заданные поля; комнатность и локации — читаемыми
        метками/именами, а не внутренними enum/объектами. Пример (из ТЗ)::

            {"rooms": "2", "price_max": 15000000,
             "metro": ["Аэропорт Внуково"], "finish": true, "sort": "price_asc"}
        """
        public: dict[str, Any] = {}

        if self.rooms:
            labels = [ROOMS_LABELS[room] for room in self.rooms]
            public["rooms"] = labels[0] if len(labels) == 1 else labels

        if self.finish:
            labels = [FINISH_LABELS[f] for f in self.finish]
            public["finish"] = labels[0] if len(labels) == 1 else labels

        scalar_fields = (
            "price_min",
            "price_max",
            "area_min",
            "area_max",
            "area_kitchen_min",
            "area_kitchen_max",
            "floor_min",
            "floor_max",
            "ready",
            "time_on_foot",
            "time_on_transport",
            "settlement_year_from",
            "settlement_year_to",
            "settlement_month_from",
            "settlement_month_to",
            "current_benefit",
            "view",
        )
        for field_name in scalar_fields:
            value = getattr(self, field_name)
            if value is not None:
                public[field_name] = value

        for flag_name in ("not_first_floor", "last_floor", "not_last_floor", "only_available"):
            if getattr(self, flag_name):
                public[flag_name] = True

        for entity_field in ("metro", "counties", "districts", "complexes"):
            entities: list[MatchedEntity] = getattr(self, entity_field)
            if entities:
                public[entity_field] = [entity.name for entity in entities]

        if self.sort is not None:
            public["sort"] = self.sort.value
        if self.housing_type is not None:
            public["housing_type"] = self.housing_type.value
        if self.option_groups:
            public["option_groups"] = list(self.option_groups)
        if self.options:
            public["options"] = list(self.options)

        return public
