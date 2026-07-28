"""PoC: вызов эндпоинта `block-with-flats`, которым живёт выдача самого pik.ru.

Разведочный скрипт, НЕ часть рантайма и НЕ замена валидатору. Задача —
доказать, что вызов работает с нашего бэкенда, и показать, что именно
возвращается. Ничего в `app/` не трогает, в `pytest` не участвует.

Эндпоинт::

    GET https://flat.pik-service.ru/api/v1/filter/block-with-flats?<query>

Найден в бандле фронта pik.ru (`pikFlatApi.fetch({method:"GET",
uri:"/api/v1/filter/block-with-flats?"...})`), подтверждён живым вызовом.
Отличается от рантаймового `api.pik.ru/v2/filter` тем, что реально применяет
`metroStations`, `districtCounties`, `districtLocations` и `hasFinish` —
те самые параметры, что перечислены в `UNVERIFIED_BY_BACKEND_PARAMS`
(`app/pik/validator.py`) как молча игнорируемые. Подробности и таблицы
замеров — `docs/measurements/2026-07-28-block-with-flats.md`.

Две ловушки, ради которых скрипт и написан (обе воспроизводятся флагами):

1. **User-Agent обязателен.** При UA, начинающемся на ``curl/``, эндпоинт
   отдаёт HTTP 200 и синтаксически правильный JSON, в котором фильтры НЕ
   применены (нефильтрованная заглушка). Никакой ошибки при этом нет —
   отличить можно только по числам. Демонстрация: ``--ua curl``.
2. **Нужен cache-buster.** Без уникального параметра примерно каждый
   четвёртый ответ приходит из общего кэша той же заглушкой. Демонстрация:
   ``--no-cache-buster --repeat 12``.

Запуск::

    uv run python -m scripts.block_with_flats_poc
    uv run python scripts/block_with_flats_poc.py --rooms 2,3 --repeat 5
    uv run python -m scripts.block_with_flats_poc --ua curl        # заглушка
    uv run python -m scripts.block_with_flats_poc --no-cache-buster --repeat 12
    uv run python -m scripts.block_with_flats_poc --json-out /tmp/bwf.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import httpx

ENDPOINT = "https://flat.pik-service.ru/api/v1/filter/block-with-flats"

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CURL_UA = "curl/8.7.1"

# location=2,3 — Москва и Московская область. Без него в выдачу попадают
# регионы (Улан-Удэ, Казань), и count расходится с тем, что показывает сайт.
MOSCOW_REGION = "2,3"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rooms", default="3", help="комнатность через запятую (по умолчанию 3)")
    parser.add_argument("--location", default=MOSCOW_REGION, help="регион (по умолчанию 2,3)")
    parser.add_argument("--metro", default=None, help="GUID станции метро через запятую")
    parser.add_argument("--blocks", default=None, help="id ЖК через запятую")
    parser.add_argument("--price-to", type=int, default=None, help="верхняя граница цены")
    parser.add_argument(
        "--ua",
        choices=("browser", "curl"),
        default="browser",
        help="'curl' воспроизводит нефильтрованную заглушку",
    )
    parser.add_argument(
        "--no-cache-buster",
        action="store_true",
        help="не добавлять уникальный параметр — воспроизводит ответы из кэша",
    )
    parser.add_argument("--repeat", type=int, default=1, help="сколько раз повторить вызов")
    parser.add_argument("--flats", type=int, default=3, help="сколько квартир печатать на ЖК")
    parser.add_argument("--json-out", type=Path, default=None, help="куда сложить сырой ответ")
    parser.add_argument("--quiet", action="store_true", help="только сводка по прогонам")
    return parser.parse_args(argv)


def build_params(args: argparse.Namespace) -> dict[str, str]:
    """Собирает query. Имена параметров — как на фронте pik.ru."""
    params: dict[str, str] = {
        "types": "1,2",
        "rooms": args.rooms,
        "location": args.location,
        # onlyFlats=1 переводит ответ в режим «ЖК + вложенные карточки квартир»
        # и наполняет stats фасетами (доступные метро/округа/ЖК/отделки).
        "onlyFlats": "1",
        "flatLimit": "50",
    }
    if args.metro:
        params["metroStations"] = args.metro
    if args.blocks:
        params["blocks"] = args.blocks
    if args.price_to is not None:
        params["priceTo"] = str(args.price_to)
    if not args.no_cache_buster:
        params["_"] = str(random.getrandbits(48))
    return params


def fetch(client: httpx.Client, params: dict[str, str], user_agent: str) -> dict[str, Any]:
    response = client.get(ENDPOINT, params=params, headers={"User-Agent": user_agent})
    response.raise_for_status()
    return response.json()


def _print_flat(flat: dict[str, Any]) -> None:
    price = flat.get("price")
    price_text = f"{price:,}".replace(",", " ") if isinstance(price, int) else str(price)
    print(
        f"      кв. {flat.get('id')}: {flat.get('rooms')}-комн., "
        f"{flat.get('area')} м², этаж {flat.get('floor')}/{flat.get('maxFloor')}, "
        f"{price_text} ₽, сдача {str(flat.get('settlementDate'))[:10]}, "
        f"отделка={flat.get('finishType')}, статус={flat.get('status')}"
    )


def print_payload(payload: dict[str, Any], flats_per_block: int) -> None:
    data = payload.get("data", {})
    stats = data.get("stats", {})
    items = data.get("items", [])

    print(f"  count (квартир) : {stats.get('count')}")
    print(f"  countBlocks (ЖК): {stats.get('countBlocks')}")
    print(f"  цена            : {stats.get('priceMin')} — {stats.get('priceMax')}")
    print(f"  площадь         : {stats.get('areaMin')} — {stats.get('areaMax')}")
    print(f"  этаж            : {stats.get('floorMin')} — {stats.get('floorMax')}")
    print(f"  страница        : {stats.get('currentPage')}/{stats.get('lastPage')}")

    facets = {
        "blocks": stats.get("blocks"),
        "finishTypes": stats.get("finishTypes"),
        "rooms": stats.get("rooms"),
        "settlements": stats.get("settlements"),
    }
    district = stats.get("district") or {}
    for key in ("metroStations", "districtCounties", "districtLocations"):
        facets[key] = district.get(key)
    print("  фасеты (сколько значений доступно при текущем фильтре):")
    for key, value in facets.items():
        size = len(value) if isinstance(value, list) else "—"
        sample = value[:3] if isinstance(value, list) else value
        print(f"      {key:<19} {size:>4}   пример: {sample}")

    print(f"  items (ЖК в выдаче): {len(items)}")
    for block in items[:3]:
        flats = block.get("flats") or []
        print(
            f"    ЖК {block.get('id')} «{block.get('name')}» "
            f"({block.get('path')}), квартир в блоке: {len(flats)}"
        )
        for flat in flats[:flats_per_block]:
            _print_flat(flat)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    user_agent = BROWSER_UA if args.ua == "browser" else CURL_UA

    print(f"Эндпоинт : {ENDPOINT}")
    print(f"UA       : {user_agent}")
    print(f"cache-buster: {'нет' if args.no_cache_buster else 'да'}   прогонов: {args.repeat}")

    counts: list[int | None] = []
    payload: dict[str, Any] = {}

    with httpx.Client(timeout=15.0) as client:
        for attempt in range(1, args.repeat + 1):
            params = build_params(args)
            if attempt == 1:
                shown = {k: v for k, v in params.items() if k != "_"}
                print(f"query    : {shown}\n")
            try:
                payload = fetch(client, params, user_agent)
            except httpx.HTTPError as exc:
                print(f"  прогон {attempt}: сетевая ошибка {type(exc).__name__}: {exc}")
                counts.append(None)
                continue

            if not payload.get("success"):
                print(f"  прогон {attempt}: success=false, {payload.get('data')}")
                counts.append(None)
                continue

            count = payload.get("data", {}).get("stats", {}).get("count")
            counts.append(count)
            if args.repeat > 1:
                print(f"  прогон {attempt:>2}: count={count}")

    if not args.quiet and payload.get("success"):
        print()
        print_payload(payload, args.flats)

    distinct = {c for c in counts if c is not None}
    if len(distinct) > 1:
        print(
            f"\nВНИМАНИЕ: разные count за один и тот же запрос — {sorted(distinct)}. "
            "Наибольшее значение — нефильтрованная заглушка из кэша."
        )
    elif args.repeat > 1:
        agreed = distinct.pop() if distinct else None
        print(f"\nВсе {args.repeat} прогонов согласованы: count={agreed}")

    if args.json_out:
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Сырой ответ: {args.json_out}")

    return 0 if any(c is not None for c in counts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
