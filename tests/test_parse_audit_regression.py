"""Регрессионный порог качества парсинга на корпусе стресс-запросов.

Замыкает контур Milestone AI-20: «комплексно решено» — это не ощущение, а
измеримое утверждение. Тест прогоняет ВЕСЬ корпус ``tests/corpus/queries.txt``
через оффлайн-харнесс ``scripts.parse_audit`` (parse → build_url, без сети и
без ИИ) и сверяет агрегаты с зафиксированным порогом. Любая будущая правка
парсера/справочников, ухудшающая целостность разбора на корпусе, роняет этот
тест ДО того, как дефект найдёт живой пользователь.

Пороги — НЕ эталонные значения «после фиксов», а границы «не хуже»: чуть
слабее фактических цифр, чтобы легитимные правки корпуса/справочников не
требовали трогать тест на каждый чих. Ужесточать по мере улучшений — можно и
нужно; ослаблять — только с явным обоснованием в PR.

Фактический замер после «ведра A» (детерминированные добивки опечаток/синонимов —
падешевле, трёха, с отделкай, кухня ат N, первый этаж, своя ванная):
crashes=0, empty_criteria=28, no_filter_urls=60, coverage=0.906.
Замер после Milestone AI-20: 32 / 64 / 0.886. ДО фиксов (дефект): 37 / 68 / 0.853.
Замер после AI-24 (алиасы ориентиров, guard маркера близости, stopwords модальности/
указательных, ведущее «этажностью», идиома «до метро пешком N минут»):
crashes=0, empty_criteria=27, no_filter_urls=60, coverage=0.9142.
Существенная часть «URL без фильтров» — законные кейсы: station-class/
landmark-запросы (сужение делает enrich(), которого оффлайн-аудит намеренно
не зовёт), болтовня без фактов и неподдерживаемые фильтры.
"""

from pathlib import Path

from scripts.parse_audit import _build_report, _load_corpus, _run_one

CORPUS = Path(__file__).parent / "corpus" / "queries.txt"

#: Пороги «не хуже» (см. докстринг модуля).
MAX_CRASHES = 0
MAX_EMPTY_CRITERIA = 28
MAX_NO_FILTER_URLS = 61
MIN_AVG_COVERAGE = 0.91


def test_corpus_regression_thresholds() -> None:
    queries = _load_corpus(CORPUS, limit=None)
    assert len(queries) >= 150, "корпус подозрительно усох — проверь queries.txt"

    report = _build_report(CORPUS, [_run_one(q) for q in queries])

    assert report.num_crashes <= MAX_CRASHES, (
        f"парсер падает на {report.num_crashes} запросах корпуса: {report.crashes[:3]}"
    )
    assert report.num_empty_criteria <= MAX_EMPTY_CRITERIA, (
        f"пустые критерии выросли: {report.num_empty_criteria} > {MAX_EMPTY_CRITERIA} "
        f"(примеры: {report.empty_criteria_queries[:5]})"
    )
    assert report.num_no_filter_urls <= MAX_NO_FILTER_URLS, (
        f"URL без фильтров выросли: {report.num_no_filter_urls} > {MAX_NO_FILTER_URLS} "
        f"(примеры: {report.no_filter_url_queries[:5]})"
    )
    assert report.avg_coverage_ratio >= MIN_AVG_COVERAGE, (
        f"покрытие текста упало: {report.avg_coverage_ratio:.3f} < {MIN_AVG_COVERAGE}"
    )
