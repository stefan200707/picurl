"""Аудит СЛОЖНЫХ многофильтровых запросов: parse → enrich (без ИИ) → build_url.

Чем отличается от ``scripts/parse_audit.py``:

``parse_audit`` проверяет ПАРСЕР — доля покрытия текста, классы warning'ов — на
коротких одно-двухфильтровых запросах. Он намеренно не зовёт ``enrich()``, и
поэтому не видит ничего, что происходит на гео-сужении: именно там живут
``blocks=`` и все дефекты вида «требование распознано, но до ссылки не доехало».

Этот скрипт гоняет ПОЛНЫЙ детерминированный путь ровно в том порядке, в каком
его выполняет ``POST /build-url`` (``app/api/endpoints.py``), с одним отличием:
``validate()`` не вызывается — это единственный сетевой шаг рантайма, а аудит
обязан быть офлайновым и воспроизводимым.

ИИ выключается принудительно (``AI_ENRICHMENT_ENABLED=False``): всё, что этот
отчёт показывает, — результат детерминированной логики, которую можно требовать
в регрессии. Ветки ориентира и класса станций работают ниже гейтов и без ИИ, они
и наполняют ``blocks``.

Формат вывода — «запрос → ссылка»: пара «что просил пользователь» / «что он
получил» — единственная форма, в которой видно, доехали ли фильтры до URL.
Сводные метрики этого не показывают.

Запуск:
    uv run python scripts/complex_audit.py
    uv run python scripts/complex_audit.py --limit 5 --json-out /tmp/out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.parse_audit import (  # noqa: E402
    _BASE_URL,
    _UNRECOGNIZED_RE,
    _load_corpus,
)

DEFAULT_CORPUS_PATH = _PROJECT_ROOT / "tests" / "corpus" / "complex_queries.txt"
DEFAULT_JSON_OUT_PATH = _PROJECT_ROOT / "graphify-out" / "complex_audit.json"


@dataclass
class ComplexQueryResult:
    """Итог одного запроса: что просили, что получили, чего не поняли."""

    text: str
    url: str = ""
    criteria: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: Имена ЖК из blocks= с дистанцией до ориентира (если ориентир был).
    matched_complexes: list[str] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    filter_count: int = 0
    coverage_ratio: float = 1.0
    ai_used: bool = False
    crashed: bool = False
    error: str = ""


@dataclass
class ComplexAuditReport:
    corpus_path: str
    total_queries: int
    num_crashes: int
    num_no_filter_urls: int
    num_without_blocks: int
    avg_coverage_ratio: float
    avg_filter_count: float
    results: list[ComplexQueryResult]


def _disable_ai() -> None:
    """Выключить ИИ-слой на весь прогон.

    ``get_settings`` кэшируется через ``lru_cache``, а ``.env`` в репозитории
    может включить ИИ обратно — поэтому сбрасываем кэш и правим уже созданный
    экземпляр, как это делают тесты проекта.
    """
    from app.config import get_settings

    get_settings.cache_clear()
    get_settings().AI_ENRICHMENT_ENABLED = False


def _landmark_distance_km(criteria, lat: float | None, lon: float | None) -> float | None:
    """Расстояние до ближайшего ориентира запроса, км (None — ориентира нет)."""
    from app.geo.distance import haversine

    if not criteria.landmark_requirements or lat is None or lon is None:
        return None
    return min(haversine(lm.lat, lm.lon, lat, lon) for lm in criteria.landmark_requirements) / 1000


async def _run_one(text: str) -> ComplexQueryResult:
    """Прогнать один запрос по тому же порядку шагов, что и ``POST /build-url``."""
    from app.ai.enrichment import enrich, merge_enrichment
    from app.parsing.parser import parse
    from app.pik.url_builder import build_url
    from app.reference.loader import load_complexes

    result = ComplexQueryResult(text=text)
    try:
        parse_result = parse(text)
        criteria = parse_result.criteria
        warnings = parse_result.warnings.copy()

        enrichment = await enrich(
            text,
            criteria,
            warnings,
            pool=None,
            option_candidates=parse_result.option_candidates,
        )
        criteria = merge_enrichment(criteria, enrichment)
        result.url = build_url(criteria, warnings)
        result.ai_used = enrichment.ai_used
        result.warnings = warnings
        result.criteria = criteria.to_public_dict()
        result.filter_count = len(result.criteria)

        by_id = {e.id: e for e in load_complexes()}
        result.block_ids = [c.id for c in criteria.complexes if c.id]
        for cid in result.block_ids:
            entry = by_id.get(cid)
            if entry is None:
                result.matched_complexes.append(f"id={cid} (нет в справочнике)")
                continue
            km = _landmark_distance_km(criteria, entry.lat, entry.lon)
            result.matched_complexes.append(
                f"{entry.name} ({km:.2f} км)" if km is not None else entry.name
            )

        uncovered = sum(
            len(m.group("phrase")) for m in _UNRECOGNIZED_RE.finditer("\n".join(warnings))
        )
        result.coverage_ratio = 1.0 if not text else max(0.0, 1.0 - uncovered / len(text))
    except Exception as exc:  # аудит обязан пережить любой запрос корпуса
        result.crashed = True
        result.error = traceback.format_exception_only(type(exc), exc)[-1].strip()
    return result


async def run_corpus(corpus_path: Path, limit: int | None = None) -> ComplexAuditReport:
    """Прогнать весь корпус и собрать отчёт. Точка входа и для скрипта, и для теста."""
    _disable_ai()
    queries = _load_corpus(corpus_path, limit)
    results = [await _run_one(q) for q in queries]

    alive = [r for r in results if not r.crashed]
    return ComplexAuditReport(
        corpus_path=str(corpus_path),
        total_queries=len(results),
        num_crashes=sum(1 for r in results if r.crashed),
        num_no_filter_urls=sum(1 for r in alive if r.url == _BASE_URL),
        num_without_blocks=sum(1 for r in alive if not r.block_ids),
        avg_coverage_ratio=(sum(r.coverage_ratio for r in alive) / len(alive) if alive else 0.0),
        avg_filter_count=(sum(r.filter_count for r in alive) / len(alive) if alive else 0.0),
        results=results,
    )


def _format_criteria(criteria: dict) -> str:
    return " | ".join(f"{k}={v}" for k, v in criteria.items()) or "—"


def _print_report(report: ComplexAuditReport) -> None:
    print("=" * 100)
    print("АУДИТ СЛОЖНЫХ ЗАПРОСОВ picurl — parse → enrich (без ИИ) → build_url, офлайн")
    print("=" * 100)
    print(f"Корпус: {report.corpus_path}")
    print(f"Всего запросов: {report.total_queries}")
    print()

    for i, r in enumerate(report.results, 1):
        print(f"[{i:02d}] ЗАПРОС: {r.text}")
        if r.crashed:
            print(f"     CRASH: {r.error}")
            print()
            continue
        print(f"     ССЫЛКА: {r.url}")
        print(f"     КРИТЕРИИ: {_format_criteria(r.criteria)}")
        if r.matched_complexes:
            print(f"     ЖК: {', '.join(r.matched_complexes)}")
        print(f"     WARNINGS: {'; '.join(r.warnings) if r.warnings else '—'}")
        print()

    print("-" * 100)
    print("ИТОГО")
    print("-" * 100)
    print(f"CRASH: {report.num_crashes}")
    print(f"URL без единого фильтра: {report.num_no_filter_urls}")
    print(f"Запросов без гео-сужения (blocks=): {report.num_without_blocks}")
    print(f"Средняя доля покрытия текста: {report.avg_coverage_ratio:.1%}")
    print(f"Среднее число фильтров на запрос: {report.avg_filter_count:.2f}")


def _write_json(report: ComplexAuditReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), "utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT_PATH)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = asyncio.run(run_corpus(args.corpus, args.limit))
    _write_json(report, args.json_out)
    if not args.quiet:
        _print_report(report)
    print(f"\nГотово: {report.total_queries} запросов, {report.num_crashes} crash(ей)")
    return 1 if report.num_crashes else 0


if __name__ == "__main__":
    raise SystemExit(main())
