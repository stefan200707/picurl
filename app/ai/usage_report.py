"""Отчёт наблюдаемости вызовов ИИ (Milestone AI-11).

Читает таблицу ``ai_call_log`` (наполняется из ``app.ai.enrichment.enrich`` при
каждом вызове) и агрегирует за период измеримые доли, по которым принимается
решение «пора возвращать fallback-гейты» (см.
``docs/ai-enrichment-architecture.md``, раздел «Критерий возврата гейтов»).

Запуск::

    python -m app.ai.usage_report              # за всё время
    python -m app.ai.usage_report --days 30    # за последние 30 дней

Аггрегация вынесена в чистую функцию :func:`aggregate` (список строк →
:class:`UsageReport`), чтобы её можно было тестировать без БД.
"""

import argparse
import asyncio
import logging
import sys

import asyncpg
from pydantic import BaseModel

from app.ai.memory import DATABASE_URL

logger = logging.getLogger(__name__)


class CallLogRow(BaseModel):
    """Одна строка ai_call_log (только поля, нужные отчёту)."""

    had_poi_or_center: bool
    fully_resolved_deterministically: bool
    cache_hit: bool
    ai_called: bool
    criteria_changed_by_ai: bool


def _pct(part: int, whole: int) -> float:
    """Доля в процентах; при нулевом знаменателе — 0.0 (нет данных = нет доли)."""
    return round(100.0 * part / whole, 1) if whole else 0.0


class UsageReport(BaseModel):
    """Агрегаты по ai_call_log за период.

    Проценты вычисляются по осмысленным знаменателям (см. докстроки свойств):
    доля запросов с гейтом 1, из НИХ доля детерминированно разрешимых (кандидат
    на «гейт 2 спас бы вызов»), доля кэш-хитов и доля вызовов с реальной пользой.
    """

    total: int
    had_poi_or_center: int
    #: fully_resolved среди запросов с had_poi_or_center (кандидаты на гейт 2).
    fully_resolved_within_had: int
    cache_hit: int
    ai_called: int
    criteria_changed_by_ai: int
    #: criteria_changed_by_ai=true среди тех, где реально звался ИИ.
    criteria_changed_within_ai_called: int
    #: had_poi_or_center=false среди criteria_changed_by_ai=true (метрика гейта 1).
    changed_without_poi_or_center: int

    @property
    def pct_had_poi_or_center(self) -> float:
        """% запросов, где сработал бы старый гейт 1 (от всех запросов)."""
        return _pct(self.had_poi_or_center, self.total)

    @property
    def pct_fully_resolved_within_had(self) -> float:
        """% детерминированно разрешимых среди had_poi_or_center — метрика гейта 2."""
        return _pct(self.fully_resolved_within_had, self.had_poi_or_center)

    @property
    def pct_cache_hit(self) -> float:
        """% ответов из семантического кэша (от всех запросов)."""
        return _pct(self.cache_hit, self.total)

    @property
    def pct_criteria_changed_within_ai_called(self) -> float:
        """% вызовов ИИ, реально изменивших criteria — реальная польза вызова."""
        return _pct(self.criteria_changed_within_ai_called, self.ai_called)

    @property
    def pct_changed_without_poi_or_center(self) -> float:
        """% «ИИ изменил criteria, но гейт 1 бы не сработал» — метрика гейта 1.

        Низкое значение = ИИ почти никогда не полезен для запросов без
        POI/центра, значит возврат гейта 1 (с учётом промпта 25) их не сломает.
        """
        return _pct(self.changed_without_poi_or_center, self.criteria_changed_by_ai)


def aggregate(rows: list[CallLogRow]) -> UsageReport:
    """Свести список строк ai_call_log в :class:`UsageReport`."""
    return UsageReport(
        total=len(rows),
        had_poi_or_center=sum(r.had_poi_or_center for r in rows),
        fully_resolved_within_had=sum(
            r.fully_resolved_deterministically and r.had_poi_or_center for r in rows
        ),
        cache_hit=sum(r.cache_hit for r in rows),
        ai_called=sum(r.ai_called for r in rows),
        criteria_changed_by_ai=sum(r.criteria_changed_by_ai for r in rows),
        criteria_changed_within_ai_called=sum(
            r.criteria_changed_by_ai and r.ai_called for r in rows
        ),
        changed_without_poi_or_center=sum(
            r.criteria_changed_by_ai and not r.had_poi_or_center for r in rows
        ),
    )


def format_report(report: UsageReport, days: int | None) -> str:
    """Человекочитаемый текст отчёта."""
    period = f"за последние {days} дн." if days else "за всё время"
    if report.total == 0:
        return f"Наблюдаемость ИИ ({period}): данных пока нет (ai_call_log пуст)."

    resolved_pct = report.pct_fully_resolved_within_had
    changed_pct = report.pct_criteria_changed_within_ai_called
    without_pct = report.pct_changed_without_poi_or_center

    had = f"{report.had_poi_or_center} ({report.pct_had_poi_or_center}%)"
    resolved = f"{report.fully_resolved_within_had} ({resolved_pct}%)"
    cache = f"{report.cache_hit} ({report.pct_cache_hit}%)"
    changed = f"{report.criteria_changed_by_ai} ({changed_pct}% от вызовов ИИ)"
    without = f"{report.changed_without_poi_or_center} ({without_pct}%)"
    lines = [
        f"Наблюдаемость вызовов ИИ ({period})",
        f"  всего запросов: {report.total}",
        f"  с POI/центром (гейт 1 сработал бы): {had}",
        f"    из них разрешимо детерминированно: {resolved}  ← кандидаты на гейт 2",
        f"  ответов из кэша (cache_hit): {cache}",
        f"  реально звался ИИ (ai_called): {report.ai_called}",
        f"  ИИ изменил criteria (польза вызова): {changed}",
        f"    из них без POI/центра (метрика гейта 1): {without}",
    ]
    return "\n".join(lines)


async def fetch_rows(pool: asyncpg.Pool, days: int | None) -> list[CallLogRow]:
    """Загрузить строки ai_call_log за период (все, если days=None)."""
    query = """
        SELECT had_poi_or_center, fully_resolved_deterministically,
               cache_hit, ai_called, criteria_changed_by_ai
        FROM ai_call_log
    """
    if days is not None:
        query += " WHERE occurred_at >= now() - ($1 || ' days')::interval"
        rows = await pool.fetch(query, str(days))
    else:
        rows = await pool.fetch(query)
    return [CallLogRow(**dict(row)) for row in rows]


async def run_report(days: int | None = None) -> None:
    pool = await asyncpg.create_pool(DATABASE_URL)
    if not pool:
        logger.error("Failed to connect to database")
        return
    try:
        rows = await fetch_rows(pool, days)
        print(format_report(aggregate(rows), days))
    finally:
        await pool.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Отчёт наблюдаемости вызовов ИИ.")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Ограничить период последними N днями (по умолчанию — за всё время).",
    )
    args = parser.parse_args()
    asyncio.run(run_report(days=args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
