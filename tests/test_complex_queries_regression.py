"""Регрессия по корпусу СЛОЖНЫХ запросов: пороги «не хуже» + точечные ожидания.

Второй замкнутый контур рядом с ``test_parse_audit_regression`` (тот сторожит
парсер на коротких запросах). Здесь сторожится ПОЛНЫЙ детерминированный путь
``parse → enrich (без ИИ) → build_url`` на длинных многофильтровых запросах —
то есть участок, где живёт ``blocks=`` и где фильтр может быть распознан, но не
доехать до ссылки. Сводные метрики парсер-аудита этого не показывают.

Пороги сняты после починки дефектов, найденных на живом запросе пользователя
(суперлатив при POI, вилка дистанции, вилка лет, дедуп POI, молчаливый откат
шорт-листа). Ужесточать по мере улучшений; ослаблять — только с обоснованием.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest  # noqa: E402

from scripts.complex_audit import run_corpus  # noqa: E402

CORPUS = Path(__file__).parent / "corpus" / "complex_queries.txt"

MAX_CRASHES = 0
#: Каждый запрос корпуса многофильтровый — URL совсем без фильтров означал бы,
#: что распался весь разбор, а не отдельный фрагмент.
MAX_NO_FILTER_URLS = 0
#: Запросы без гео-сужения. Не ноль честно: часть запросов сужается не по blocks
#: (округ/метро идут своими параметрами), часть упирается в потолок данных.
MAX_WITHOUT_BLOCKS = 9
MIN_AVG_COVERAGE = 0.89
MIN_AVG_FILTER_COUNT = 8.8


@pytest.fixture(scope="module")
async def report():
    return await run_corpus(CORPUS, limit=None)


async def test_complex_corpus_thresholds(report):
    assert len(report.results) >= 30, "корпус подозрительно усох — проверь complex_queries.txt"

    crashed = [r.text for r in report.results if r.crashed]
    assert report.num_crashes <= MAX_CRASHES, f"появились падения: {crashed}"

    no_filters = [r.text for r in report.results if r.url.endswith("/search")]
    assert report.num_no_filter_urls <= MAX_NO_FILTER_URLS, f"URL без фильтров: {no_filters}"

    assert report.num_without_blocks <= MAX_WITHOUT_BLOCKS, (
        f"стало больше запросов без гео-сужения: {report.num_without_blocks} > {MAX_WITHOUT_BLOCKS}"
    )
    assert report.avg_coverage_ratio >= MIN_AVG_COVERAGE, (
        f"покрытие текста упало: {report.avg_coverage_ratio:.3f} < {MIN_AVG_COVERAGE}"
    )
    assert report.avg_filter_count >= MIN_AVG_FILTER_COUNT, (
        f"фильтров на запрос стало меньше: {report.avg_filter_count:.2f} < {MIN_AVG_FILTER_COUNT}"
    )


async def test_reference_query_unchanged(report):
    """Запрос №1 — живой запрос пользователя, эталон «не сломать работавшее».

    Эти три ЖК были верным ответом ДО починки (совпадение суперлатива и радиуса —
    один из 5 случаев на 50 ориентиров). Правки обязаны его сохранить, добавив
    лишь то, что раньше терялось: верхнюю границу лет, дистанцию до садов и
    честный warning о суперлативе.
    """
    first = report.results[0]

    assert first.block_ids == ["481", "1580", "1460"]
    assert first.criteria["settlement_year_to"] == 2029
    kindergarten = [
        p for p in first.criteria["poi_requirements"] if p["category"] == "kindergarten"
    ]
    assert len(kindergarten) == 1, "дубль POI-требования вернулся"
    assert kindergarten[0]["max_distance_m"] == 1600
    assert any("ближайшие к" in w for w in first.warnings)


async def test_superlative_with_poi_returns_complexes(report):
    """Запрос №2 («максимально близко к МФТИ» + сады) — антипример дефекта.

    До починки: nearest_only не выставлялся, радиус 5 км не давал ни одного ЖК,
    ответ был пуст. После: суперлатив применяется поверх POI-требования.
    """
    second = report.results[1]

    assert second.block_ids, "суперлатив при наличии POI снова потерялся"
    assert second.criteria["landmark_requirements"][0]["nearest_only"] is True
