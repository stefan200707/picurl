"""Обновление JSON-справочников с backend-API pik.ru.

CLI-скрипт: ``python -m app.reference.refresh``. Это **единственное место**
проекта (кроме валидатора выдачи, промпт 08), где допустим сетевой доступ;
запускается вручную/по расписанию, **не** в рантайме запроса.

Источник данных — открытый backend ``api.pik.ru/v2/block`` (список всех ЖК с
метаданными). Из него собираются:

- ``complexes.json`` — обе URL-формы целиком: слаг (``url``) и числовой id
  (тот самый id, что уходит в query-параметр ``blocks``; подтверждено:
  «Мичуринский парк» → ``/mpark`` / ``blocks=1108``);
- ``counties.json`` — имена и слаги округов Москвы (``locations.child.url``,
  например ``zao``);
- ``metro.json`` / ``districts.json`` — имена станций/районов, встречающихся
  у ЖК (без слагов/id).

Ограничение: GUID-ы станций для ``metroStations`` и числовые id округов для
``districtCounties`` отдаёт только front-API ``www.pik.ru`` (закрыт
бот-защитой Qrator), поэтому эти поля ведутся вручную в JSON. Скрипт **не
затирает** ручную докурацию: записи мёржатся по слагу/имени, кураторские
``slug``/``id``/``aliases`` сохраняются, файлы перезаписываются идемпотентно
со стабильной сортировкой (диффы читаемы). ``benefits.json`` /
``option_groups.json`` / ``options.json`` этим эндпоинтом не покрываются и
не трогаются.
"""

import json
import sys
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, RootModel

from app.reference.loader import DATA_DIR, REFERENCE_FILES, RefEntry, normalize

#: Открытый backend-эндпоинт pik.ru со списком всех ЖК.
BLOCKS_URL = "https://api.pik.ru/v2/block"
BLOCKS_PARAMS: dict[str, str] = {"types": "1,2", "metadata": "1"}

#: Справочники, которые этот скрипт умеет обновлять.
REFRESHABLE = ("complexes", "counties", "metro", "districts")


class LocationChild(BaseModel):
    name: str | None = None
    url: str | None = None


class LocationParent(BaseModel):
    name: str | None = None


class Locations(BaseModel):
    parent: LocationParent | None = None
    child: LocationChild | None = None


class BlockPayload(BaseModel):
    id: int | None = None
    name: str | None = None
    url: str | None = None
    locations: Locations | None = None
    metro: str | None = None
    district: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class BlocksResponse(RootModel[list[BlockPayload]]):
    pass


def fetch_blocks(client: httpx.Client) -> list[BlockPayload]:
    """Скачать список ЖК с ``api.pik.ru/v2/block``."""
    response = client.get(BLOCKS_URL, params=BLOCKS_PARAMS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"Неожиданный ответ {BLOCKS_URL}: ожидался список ЖК")
    return BlocksResponse.model_validate(payload).root


async def fetch_blocks_async(client: httpx.AsyncClient) -> list[BlockPayload]:
    """Асинхронная версия скачивания списка ЖК."""
    response = await client.get(BLOCKS_URL, params=BLOCKS_PARAMS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"Неожиданный ответ {BLOCKS_URL}: ожидался список ЖК")
    return BlocksResponse.model_validate(payload).root


def complexes_from_blocks(blocks: list[BlockPayload]) -> list[RefEntry]:
    """ЖК: имя, слаг (``url`` без ведущего ``/``), id, координаты и гео-привязка.

    Привязку к району (``block.district``), метро (``block.metro``) и округу
    Москвы (``locations.child.name``) сохраняем прямо на записи ЖК — она нужна
    ИИ-слою для формирования кандидатов с реальным гео-контекстом. Округ берём
    только для Москвы (как и :func:`counties_from_blocks`).
    """
    entries: list[RefEntry] = []
    for block in blocks:
        if not block.name:
            continue
        url = block.url or ""
        slug = url.strip("/") or None
        county = None
        if (
            block.locations
            and block.locations.parent
            and block.locations.parent.name == "Москва"
            and block.locations.child
        ):
            county = block.locations.child.name
        entries.append(
            RefEntry(
                name=block.name,
                slug=slug,
                id=str(block.id) if block.id is not None else None,
                lat=block.latitude,
                lon=block.longitude,
                district=block.district,
                county=county,
                metro=block.metro,
            )
        )
    return _dedupe(entries)


def counties_from_blocks(blocks: list[BlockPayload]) -> list[RefEntry]:
    """Округа Москвы: имя и слаг из ``locations.child`` (id front-API не отдаёт)."""
    entries: list[RefEntry] = []
    for block in blocks:
        if not block.locations or not block.locations.parent or not block.locations.child:
            continue
        if block.locations.parent.name != "Москва":
            continue
        name = block.locations.child.name
        if not name:
            continue
        entries.append(RefEntry(name=name, slug=block.locations.child.url or None))
    return _dedupe(entries)


def metro_from_blocks(blocks: list[BlockPayload]) -> list[RefEntry]:
    """Станции метро, упомянутые у ЖК (только имена; слаг/GUID — вручную)."""
    return _dedupe(RefEntry(name=block.metro) for block in blocks if block.metro)


def districts_from_blocks(blocks: list[BlockPayload]) -> list[RefEntry]:
    """Районы, упомянутые у ЖК (только имена; id для ``districtLocations`` — вручную)."""
    return _dedupe(RefEntry(name=block.district) for block in blocks if block.district)


def _dedupe(entries: Any) -> list[RefEntry]:
    """Убрать дубликаты (по нормализованному имени), сохранив первое вхождение."""
    seen: set[str] = set()
    result: list[RefEntry] = []
    for entry in entries:
        key = normalize(entry.name)
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


def merge_entries(
    existing: list[RefEntry], fetched: list[RefEntry], kind: str = ""
) -> list[RefEntry]:
    """Смёржить свежие данные с кураторскими, ничего не теряя.

    Записи сопоставляются по слагу, затем по нормализованному имени/алиасу.
    Свежие ``name``/``slug``/``id`` обновляют запись, но отсутствующие в ответе
    поля и кураторские ``aliases`` сохраняются. Незатронутые ручные записи
    остаются как есть. Результат стабильно отсортирован по имени.
    """
    merged = list(existing)
    by_slug = {entry.slug: index for index, entry in enumerate(merged) if entry.slug}
    by_name: dict[str, int] = {}
    for index, entry in enumerate(merged):
        by_name.setdefault(normalize(entry.name), index)
        for alias in entry.aliases:
            by_name.setdefault(normalize(alias), index)

    for entry in fetched:
        slug_index = by_slug.get(entry.slug) if entry.slug else None
        index = slug_index if slug_index is not None else by_name.get(normalize(entry.name))
        if index is None:
            # Для метро используем полный статический справочник, не собираем с нуля из API
            if kind == "metro":
                continue
            merged.append(entry)
            continue
        current = merged[index]
        merged[index] = current.model_copy(
            update={
                # Официальное имя обновляем только при совпадении по слагу
                # (переименование на сайте); совпадение по имени/алиасу не должно
                # затирать каноническое кураторское имя.
                "name": entry.name if slug_index is not None else current.name,
                "slug": entry.slug or current.slug,
                "id": entry.id or current.id,
                "lat": entry.lat or current.lat,
                "lon": entry.lon or current.lon,
                "district": entry.district or current.district,
                "county": entry.county or current.county,
                "metro": entry.metro or current.metro,
            }
        )

    return sorted(merged, key=lambda entry: (normalize(entry.name), entry.slug or ""))


def write_entries(path: Path, entries: list[RefEntry]) -> None:
    """Записать справочник на диск (utf-8, отступы, стабильный порядок полей)."""
    payload = [entry.model_dump(mode="json", exclude_defaults=True) for entry in entries]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_existing(path: Path) -> list[RefEntry]:
    """Прочитать текущий JSON-справочник (пустой список, если файла ещё нет)."""
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [RefEntry.model_validate(item) for item in raw]


def _merge_and_write(blocks: list[BlockPayload], data_dir: Path) -> dict[str, int]:
    """Общий хвост refresh/run_refresh (AUDIT_REPORT 2.4): сборка справочников
    из блоков, merge с кураторскими данными и запись на диск.

    Возвращает итоговые размеры по файлам.
    """
    fetched_by_kind: dict[str, list[RefEntry]] = {
        "complexes": complexes_from_blocks(blocks),
        "counties": counties_from_blocks(blocks),
        "metro": metro_from_blocks(blocks),
        "districts": districts_from_blocks(blocks),
    }

    counts: dict[str, int] = {}
    for kind in REFRESHABLE:
        path = data_dir / REFERENCE_FILES[kind]
        merged = merge_entries(load_existing(path), fetched_by_kind[kind], kind)
        write_entries(path, merged)
        counts[kind] = len(merged)
    return counts


def refresh(client: httpx.Client, data_dir: Path = DATA_DIR) -> dict[str, int]:
    """Обновить справочники в ``data_dir``; вернуть итоговые размеры по файлам."""
    blocks = fetch_blocks(client)
    return _merge_and_write(blocks, data_dir)


async def run_refresh(data_dir: Path = DATA_DIR) -> dict[str, int]:
    """Асинхронная обёртка для использования в эндпоинтах."""
    headers = {"User-Agent": "picurl-refresh/0.1 (+https://github.com/stefan200707/picurl)"}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        blocks = await fetch_blocks_async(client)

    return _merge_and_write(blocks, data_dir)


def main() -> int:
    """Точка входа CLI: скачать данные, перезаписать JSON, напечатать сводку."""
    headers = {"User-Agent": "picurl-refresh/0.1 (+https://github.com/stefan200707/picurl)"}
    with httpx.Client(timeout=30, headers=headers) as client:
        counts = refresh(client)

    for kind in REFRESHABLE:
        print(f"{REFERENCE_FILES[kind]}: {counts[kind]} записей")
    print(
        "Напоминание: GUID-ы метро (metroStations) и id округов (districtCounties) "
        "front-API не отдаёт — докуривать вручную; benefits/option_groups/options "
        "не обновляются этим скриптом."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
