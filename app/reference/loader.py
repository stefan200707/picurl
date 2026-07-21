"""Загрузка локальных JSON-справочников в память.

Справочники (метро/округа/районы/ЖК/программы/опции) лежат рядом с модулем в
``app/reference/*.json`` и обновляются скриптом ``python -m app.reference.refresh``
(см. :mod:`app.reference.refresh`). Рантайм читает их только с диска — в сеть за
ними **не ходит** (архитектурный инвариант №4).

Каждая запись — :class:`RefEntry`: человекочитаемое имя, слаг для single-выбора
в пути URL, id/GUID для multi-выбора в query (закономерность single-путь /
multi-query, см. ``docs/pik-url-schema.md``) и разговорные алиасы для матчинга.
`slug`/`id` могут отсутствовать: часть значений ещё не собрана (front-API
pik.ru закрыт бот-защитой) — недостающие формы `url_builder` обязан отправлять
в ``warnings``, а не отбрасывать молча.

Загрузка кэшируется (``functools.cache``); единая точка для матчера
(промпт 05) — :func:`load_all`.
"""

import json
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict

#: Директория с JSON-справочниками (рядом с модулем).
DATA_DIR = Path(__file__).parent

#: Имена файлов справочников (ключ — имя поля в :class:`ReferenceData`).
REFERENCE_FILES: dict[str, str] = {
    "metro": "metro.json",
    "counties": "counties.json",
    "districts": "districts.json",
    "complexes": "complexes.json",
    "benefits": "benefits.json",
    "option_groups": "option_groups.json",
    "options": "options.json",
}


class RefEntry(BaseModel):
    """Запись справочника: имя + обе URL-формы (слаг и id/GUID) + алиасы.

    ``id`` хранится строкой: формат един для GUID метро и числовых id
    округов/районов/ЖК (то же решение, что в ``MatchedEntity``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    slug: str | None = None
    id: str | None = None
    aliases: tuple[str, ...] = ()
    lat: float | None = None
    lon: float | None = None
    is_center: bool | None = None
    # Привязка ЖК к локации (заполняется только для записей complexes.json из
    # ответа api.pik.ru: block.district / block.metro / locations.child.name).
    # Для остальных справочников остаётся None. Нужна ИИ-слою, чтобы кандидаты
    # уходили в модель с реальным гео-контекстом, а не пустыми полями.
    district: str | None = None
    county: str | None = None
    metro: str | None = None


class ReferenceData(BaseModel):
    """Агрегат всех справочников — удобная единая точка для матчера (промпт 05)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metro: tuple[RefEntry, ...]
    counties: tuple[RefEntry, ...]
    districts: tuple[RefEntry, ...]
    complexes: tuple[RefEntry, ...]
    benefits: tuple[RefEntry, ...]
    option_groups: tuple[RefEntry, ...]
    options: tuple[RefEntry, ...]


def normalize(text: str) -> str:
    """Нормализовать строку для сравнения имён/алиасов: casefold, ё→е, пробелы."""
    return " ".join(text.casefold().replace("ё", "е").split())


@cache
def _load(filename: str) -> tuple[RefEntry, ...]:
    """Прочитать и провалидировать один JSON-справочник (с кэшированием)."""
    raw = json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))
    return tuple(RefEntry.model_validate(item) for item in raw)


def load_metro() -> tuple[RefEntry, ...]:
    """Станции метро (slug вида ``m-...``, id — GUID для ``metroStations``)."""
    return _load(REFERENCE_FILES["metro"])


def load_counties() -> tuple[RefEntry, ...]:
    """Округа Москвы (slug вида ``zao``, id для ``districtCounties``)."""
    return _load(REFERENCE_FILES["counties"])


def load_districts() -> tuple[RefEntry, ...]:
    """Районы (только id для ``districtLocations`` — район всегда уходит в query)."""
    return _load(REFERENCE_FILES["districts"])


def load_complexes() -> tuple[RefEntry, ...]:
    """ЖК (slug для пути + числовой id для ``blocks``)."""
    return _load(REFERENCE_FILES["complexes"])


def load_benefits() -> tuple[RefEntry, ...]:
    """Программы покупки — слаги ``currentBenefit``."""
    return _load(REFERENCE_FILES["benefits"])


def load_option_groups() -> tuple[RefEntry, ...]:
    """«Особенности планировки» — слаги ``optionGroups``."""
    return _load(REFERENCE_FILES["option_groups"])


def load_options() -> tuple[RefEntry, ...]:
    """«Вид из окна» — слаги ``options``."""
    return _load(REFERENCE_FILES["options"])


def load_all() -> ReferenceData:
    """Загрузить все справочники разом (для матчера, промпт 05)."""
    return ReferenceData(
        metro=load_metro(),
        counties=load_counties(),
        districts=load_districts(),
        complexes=load_complexes(),
        benefits=load_benefits(),
        option_groups=load_option_groups(),
        options=load_options(),
    )


def find_by_name(entries: tuple[RefEntry, ...], query: str) -> RefEntry | None:
    """Точный (после нормализации) поиск записи по имени или алиасу.

    Возвращает первую подходящую запись или ``None``. Fuzzy-поиск — задача
    матчера (промпт 05), здесь только exact-match для простых случаев и тестов.
    """
    needle = normalize(query)
    for entry in entries:
        if normalize(entry.name) == needle:
            return entry
        if any(normalize(alias) == needle for alias in entry.aliases):
            return entry
    return None


def clear_cache() -> None:
    """Сбросить кэш загрузки (после обновления JSON, в основном для тестов)."""
    _load.cache_clear()
