"""Прогонщик сложных запросов к picurl через ``POST /build-url``.

Скрипт гоняет набор из 30 запутанных запросов (двушки/трёшки, метро, бюджет
в смешанных единицах, отделка, «рядом с парком/школой», отрицания, опечатки,
сленг) прямо через ``fastapi.testclient.TestClient`` — БЕЗ поднятия сервера.
Для каждого запроса печатает: сам запрос, полученный URL и распознанные фильтры
(``criteria``) вместе с warnings.

Особенности окружения:

* ИИ-слой в этом окружении недоступен (нет кредов и БД). Скрипт защитно
  выставляет ``AI_ENRICHMENT_ENABLED=false`` ДО импорта приложения — отказы
  ИИ здесь дефектом не считаются.
* Валидатор pik.ru (единственный сетевой вызов рантайма) замокан через
  ``httpx.MockTransport`` — прогон не зависит от сети и детерминирован.

Запуск::

    AI_ENRICHMENT_ENABLED=false uv run python scripts/query_smoke.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ИИ-слой в этом окружении недоступен — выключаем до импорта приложения, чтобы
# get_settings() (кэшируется) увидел выключенный ИИ.
os.environ.setdefault("AI_ENRICHMENT_ENABLED", "false")

# Обычный запуск скрипта не имеет pytest-настройки ``pythonpath=.`` — добавляем
# корень репозитория в sys.path, чтобы работал ``import app``.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

#: 30 запутанных запросов для проверки устойчивости парсера.
QUERIES: list[str] = [
    # --- комнатность + бюджет в смешанных единицах ---
    "хочу двушку у метро до 15 млн с отделкой",
    "трёшка до 20 лямов, подешевле",
    "однушка за 8000000 без отделки",
    "студия до 6.5 млн рядом с метро",
    "нужна 3-комнатная от 60 метров, бюджет 25 млн",
    "2-х комнатную квартиру, цена от 10 до 18 млн",
    "четырёхкомнатная, площадь побольше",
    "1-2 комнатные до 12 млн, чистовая отделка",
    "двушка бюджет 14 лямов, кухня от 12 метров",
    "трёшку за 22 млн, высокий этаж, с отделкой",
    # --- метро / локации ---
    "квартира рядом с метро Тульская до 17 млн",
    "двушка в районе Хамовники, подороже",
    "однушка около метро ЦСКА, готовый дом",
    "хочу трёшку в ЮЗАО до 30 млн",
    "квартира у метро Аэропорт, с 5 по 20 этаж",
    # --- POI / гео / ориентиры ---
    "двушка рядом с парком до 16 млн",
    "трёшка рядом со школой и детским садом",
    "квартира рядом с МГУ, до 25 млн",
    "однушка недалеко от парка, не первый этаж",
    "двушка возле метро и рядом с поликлиникой до 15 млн",
    # --- отрицания ---
    "двушка не первый и не последний этаж до 14 млн",
    "квартира без апартаментов, только квартиры до 12 млн",
    "трёшка, только свободные, не бронь",
    "однушка без отделки, не последний этаж",
    # --- опечатки / сленг / смешанное ---
    "хачу двушку у митро падешевле да 15 млн",
    "трешка 800к первый взнос, ипотека, до 20 млн",  # 800к — не цена квартиры
    "нужна хата двушка, бюджет 13 лямов, с ремонтом",
    "квартира с отдельным санузлом до 18 млн",  # синоним фильтра
    "вторичка двушка до 15 млн у метро",  # неподдерживаемое pik.ru
    "панельный дом, трёшка, кирпичный, до 20 млн",  # неподдерживаемый материал
]


def build_mock_client() -> httpx.AsyncClient:
    """AsyncClient с замоканным backend-API pik.ru (детерминированный count)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 42})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def format_criteria(criteria: dict) -> str:
    """Компактно отрендерить непустые распознанные фильтры."""
    if not criteria:
        return "(ничего не распознано)"
    parts = [f"{k}={v!r}" for k, v in criteria.items()]
    return ", ".join(parts)


def main() -> int:
    with TestClient(app) as client:
        # Подменяем сетевой http-клиент валидатора на замоканный (без сети).
        client.app.state.http_client = build_mock_client()

        for i, query in enumerate(QUERIES, start=1):
            print(f"\n{'=' * 78}")
            print(f"[{i:02d}/30] Запрос: {query}")
            print("-" * 78)
            resp = client.post("/build-url", json={"text": query})
            if resp.status_code != 200:
                print(f"  !! HTTP {resp.status_code}: {resp.text}")
                continue
            data = resp.json()
            print(f"  URL:      {data['url']}")
            print(f"  Фильтры:  {format_criteria(data['criteria'])}")
            warnings = data.get("warnings") or []
            if warnings:
                print("  Warnings:")
                for w in warnings:
                    print(f"    - {w}")
            else:
                print("  Warnings: —")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
