"""Офлайн-сбор POI-кэша из OSM Overpass (сеть; в рантайме не вызывается).

Запуск: ``uv run python -m app.geo.refresh_poi`` (``--force`` — пересобрать все
записи, даже уже актуальной схемы).

Собираем радиусом 2000 м, чтобы кэш покрывал любой пользовательский
``max_distance_m``, и СРАЗУ разделяем объекты на действующие и строящиеся
(:func:`app.geo.poi.is_operational`): дистанция кэша — только по действующим,
иначе «садик в 200 метрах» проходит по стройплощадке. Записи прошлой схемы
(``schema_version < POI_CACHE_SCHEMA_VERSION``) пересобираются автоматически.

Публичный Overpass под нагрузкой отдаёт 429/504 — транзиентные отказы
ретраятся с backoff; при полном провале конкретной записи СТАРОЕ значение
остаётся нетронутым, а имя ЖК/категория попадают в итоговый отчёт.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.geo.poi import POI_CACHE_SCHEMA_VERSION, POICategory, fetch_poi
from app.reference.loader import load_complexes

CACHE_FILE = Path(__file__).parent.parent / "reference" / "poi_cache.json"

#: Радиус сбора: кэш должен покрывать любую пользовательскую отсечку дистанции.
COLLECT_RADIUS_M = 2000

#: Сколько раз пробовать одну запись при транзиентных отказах Overpass.
MAX_ATTEMPTS = 4

#: База экспоненциального backoff (сек): 2, 4, 8… Публичное зеркало под
#: нагрузкой отвечает 429/504, и частые повторы делают только хуже.
RETRY_BASE_DELAY_S = 2.0

#: Пауза между удачными запросами — вежливость к публичному зеркалу.
POLITE_DELAY_S = 1.0

#: Сколько запросов держать в воздухе одновременно. Замер 2026-07: один запрос
#: к maps.mail.ru отвечает ~20-25 с, то есть строго последовательный сбор всех
#: 414 записей занимает ~2.5 часа. Публичный Overpass выдаёт ограниченное число
#: слотов на IP, поэтому 3 — компромисс: втрое быстрее и всё ещё вежливо
#: (лишнее сверх слота вернётся 429 и уйдёт в ретрай с backoff, а не потеряется).
MAX_CONCURRENCY = 3

#: Как часто сбрасывать кэш на диск (в записях): прерванный сбор не должен
#: терять уже полученное.
FLUSH_EVERY = 12


def _needs_refresh(entry: object, force: bool) -> bool:
    if force or not isinstance(entry, dict):
        return True
    return int(entry.get("schema_version", 1)) < POI_CACHE_SCHEMA_VERSION


async def _fetch_with_retries(lat: float, lon: float, category: POICategory) -> dict:
    """Собрать одну запись, ретраясь на транзиентных отказах зеркал."""
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            result = await fetch_poi(lat, lon, category, COLLECT_RADIUS_M)
            return result.model_dump()
        except Exception as e:  # httpx/JSON — все зеркала уже перебраны внутри
            last_error = e
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY_S * 2**attempt
                print(f"  отказ Overpass ({e}); повтор через {delay:.0f} с")
                await asyncio.sleep(delay)
    raise last_error if last_error else RuntimeError("Overpass не ответил")


def _write_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", "utf-8")


async def refresh_all_pois(force: bool = False) -> int:
    complexes = load_complexes()

    cache = json.loads(CACHE_FILE.read_text("utf-8")) if CACHE_FILE.exists() else {}

    todo: list[tuple[str, str, float, float, POICategory]] = []
    for block in complexes:
        if not block.lat or not block.lon or not block.slug:
            continue
        cache.setdefault(block.slug, {})
        for category in POICategory:
            if category == POICategory.OTHER:
                continue
            if not _needs_refresh(cache[block.slug].get(category.value), force):
                continue
            todo.append((block.slug, block.name, block.lat, block.lon, category))

    print(f"К сбору {len(todo)} записей (параллельно {MAX_CONCURRENCY}).")

    failures: list[str] = []
    done = 0
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(slug: str, name: str, lat: float, lon: float, category: POICategory) -> None:
        nonlocal done
        async with semaphore:
            try:
                entry = await _fetch_with_retries(lat, lon, category)
            except Exception as e:
                # Старое значение НЕ трогаем: неполный кэш лучше стёртого.
                failures.append(f"{name} / {category.value}: {e}")
                print(f"Failed to fetch for {name} / {category}: {e}", flush=True)
                return
            cache[slug][category.value] = entry
            done += 1
            print(
                f"[{done + len(failures)}/{len(todo)}] {name} / {category.value}: "
                f"действующих {entry['count_operational']}, "
                f"стройка {entry['count_under_construction']}, "
                f"ближайший {entry['closest_distance_m']}",
                flush=True,
            )
            if done % FLUSH_EVERY == 0:
                _write_cache(cache)
            await asyncio.sleep(POLITE_DELAY_S)

    await asyncio.gather(*(worker(*item) for item in todo))
    _write_cache(cache)

    print(f"Refresh POI completed: обновлено записей {done}, отказов {len(failures)}.")
    for line in failures:
        print(f"  НЕ обновлено: {line}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Собрать POI-кэш из OSM Overpass")
    parser.add_argument(
        "--force",
        action="store_true",
        help="пересобрать все записи, включая уже актуальной схемы",
    )
    args = parser.parse_args()
    return asyncio.run(refresh_all_pois(force=args.force))


if __name__ == "__main__":
    sys.exit(main())
