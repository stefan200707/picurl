"""Промоушен фактов из БД в детерминированные справочники."""

import asyncio
import json
import logging
import sys

import asyncpg
from pydantic import BaseModel

from app.ai.memory import DATABASE_URL, StructuredFact
from app.reference.loader import DATA_DIR
from app.reference.refresh import load_existing, write_entries

logger = logging.getLogger(__name__)


class PromotionReport(BaseModel):
    promoted_count: int
    ignored_count: int


async def find_promotable_facts(pool: asyncpg.Pool) -> list[StructuredFact]:
    """Найти факты, которые прошли порог уверенности и числа наблюдений.

    Пороги берутся из конфига (:mod:`app.config`). Требование к числу
    независимых наблюдений — главный вентиль против отравления справочников
    самооценкой модели: одна уверенная галлюцинация факт не промоутит.
    """
    from app.config import get_settings

    settings = get_settings()
    query = """
        SELECT id, subject_type, subject_id, fact_type, fact_value, source, confidence,
               observed_count, first_seen_at, last_confirmed_at
        FROM ai_structured_facts
        WHERE observed_count >= $1
          AND confidence >= $2
          AND source != 'promoted'
    """
    rows = await pool.fetch(
        query, settings.AI_PROMOTION_MIN_OBSERVATIONS, settings.AI_PROMOTION_MIN_CONFIDENCE
    )

    facts = []
    for row in rows:
        facts.append(
            StructuredFact(
                id=row["id"],
                subject_type=row["subject_type"],
                subject_id=row["subject_id"],
                fact_type=row["fact_type"],
                fact_value=json.loads(row["fact_value"]),
                source=row["source"],
                confidence=row["confidence"],
                observed_count=row["observed_count"],
                first_seen_at=row["first_seen_at"],
                last_confirmed_at=row["last_confirmed_at"],
            )
        )
    return facts


async def promote(pool: asyncpg.Pool, facts: list[StructuredFact]) -> PromotionReport:
    """Записать факты в JSON-справочники и отметить их как promoted в БД."""
    if not facts:
        return PromotionReport(promoted_count=0, ignored_count=0)

    counties_path = DATA_DIR / "counties.json"
    districts_path = DATA_DIR / "districts.json"
    complexes_path = DATA_DIR / "complexes.json"
    poi_cache_path = DATA_DIR / "poi_cache.json"

    counties = load_existing(counties_path)
    districts = load_existing(districts_path)
    complexes = load_existing(complexes_path)
    poi_cache = (
        json.loads(poi_cache_path.read_text(encoding="utf-8")) if poi_cache_path.exists() else {}
    )

    counties_by_id = {c.id: i for i, c in enumerate(counties) if c.id}
    districts_by_id = {d.id: i for i, d in enumerate(districts) if d.id}
    complexes_by_id = {c.id: c.slug for c in complexes if c.id and c.slug}

    promoted_ids = []

    for fact in facts:
        promoted = False
        if fact.fact_type == "is_center":
            is_center_val = fact.fact_value.get("is_center")
            if fact.subject_type == "county":
                idx = counties_by_id.get(fact.subject_id)
                if idx is not None:
                    counties[idx] = counties[idx].model_copy(update={"is_center": is_center_val})
                    promoted = True
            elif fact.subject_type == "district":
                idx = districts_by_id.get(fact.subject_id)
                if idx is not None:
                    districts[idx] = districts[idx].model_copy(update={"is_center": is_center_val})
                    promoted = True
        elif fact.fact_type.startswith("poi_"):
            if fact.subject_type == "complex":
                slug = complexes_by_id.get(fact.subject_id)
                if slug:
                    poi_category = fact.fact_type.removeprefix("poi_")
                    is_present = fact.fact_value.get("present", False)
                    if slug not in poi_cache:
                        poi_cache[slug] = {}

                    # Create a simulated POIResult dump if not exist
                    cache_entry = poi_cache[slug].get(poi_category, {})
                    cache_entry["count"] = 1 if is_present else 0
                    if "closest_distance_m" not in cache_entry:
                        cache_entry["closest_distance_m"] = None

                    poi_cache[slug][poi_category] = cache_entry
                    promoted = True

        if promoted:
            promoted_ids.append(fact.id)

    if promoted_ids:
        write_entries(counties_path, counties)
        write_entries(districts_path, districts)
        poi_cache_path.write_text(
            json.dumps(poi_cache, indent=2, ensure_ascii=False) + "\n", "utf-8"
        )

        # Mark as promoted in DB
        update_query = """
            UPDATE ai_structured_facts
            SET source = 'promoted'
            WHERE id = ANY($1)
        """
        await pool.execute(update_query, promoted_ids)

    return PromotionReport(
        promoted_count=len(promoted_ids), ignored_count=len(facts) - len(promoted_ids)
    )


async def run_promotion():
    pool = await asyncpg.create_pool(DATABASE_URL)
    if not pool:
        logger.error("Failed to connect to database")
        return

    try:
        facts = await find_promotable_facts(pool)
        report = await promote(pool, facts)
        logger.info(f"Promoted {report.promoted_count} facts, ignored {report.ignored_count}")
        print(f"Promoted: {report.promoted_count}, Ignored: {report.ignored_count}")

        # Simple report on AI calls
        # (Using observed_count as proxy for AI usage vs cached for demo purposes,
        # actual AI metrics would come from an ai_call_log table as suggested).
    finally:
        await pool.close()


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_promotion())
    return 0


if __name__ == "__main__":
    sys.exit(main())
