"""Промоушен фактов из БД в детерминированные справочники."""

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict

import asyncpg
from pydantic import BaseModel

from app.ai.memory import StructuredFact
from app.config import get_settings
from app.reference.loader import DATA_DIR, normalize
from app.reference.refresh import load_existing, write_entries

logger = logging.getLogger(__name__)

#: fact_type, под которым логируются сопоставления «фраза → slug опции»
#: (см. app/ai/enrichment.OPTION_ALIAS_FACT_TYPE).
OPTION_ALIAS_FACT_TYPE = "option_alias"

#: Куда промоутить алиас в зависимости от типа субъекта факта.
ALIAS_FILES = {"option_group": "option_groups.json", "option": "options.json"}


class PromotionReport(BaseModel):
    promoted_count: int
    ignored_count: int


class AmbiguousAlias(BaseModel):
    """Фраза с расходящимися наблюдениями — конфликт, требует ручного решения."""

    phrase: str
    slug_counts: dict[str, int]


class PromotableAlias(BaseModel):
    phrase: str
    subject_type: str
    slug: str
    observed_count: int
    confidence: float
    fact_ids: list[int]


class AliasPromotionReport(BaseModel):
    promoted: list[tuple[str, str]] = []  # (phrase, slug)
    ambiguous: list[AmbiguousAlias] = []


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


def analyze_option_aliases(
    facts: list[StructuredFact],
    min_observations: int,
    min_confidence: float,
    min_consistency: float,
) -> tuple[list[PromotableAlias], list[AmbiguousAlias]]:
    """Разложить наблюдения алиасов на промоутируемые и неоднозначные.

    Ключевая защита от неоднозначности (отличие от гео-фактов промпта 20):
    смотрим ВСЕ наблюдения одной нормализованной фразы (не только по конкретному
    slug). Если доля наблюдений, ведущих к доминирующему slug, ниже
    ``min_consistency`` — фраза расходится между разными slug, это конфликт по
    определению промпта 17: не промоутится никогда, независимо от числа
    наблюдений/уверенности, и уходит в отчёт «неоднозначные». При
    ``min_consistency == 1.0`` (дефолт) любое расхождение = конфликт.

    Согласованная фраза дополнительно проходит обычный вентиль наблюдений/
    уверенности из промпта 20; не набравшая порога тихо остаётся на ИИ.
    """
    by_phrase: dict[str, list[StructuredFact]] = defaultdict(list)
    for fact in facts:
        if fact.fact_type != OPTION_ALIAS_FACT_TYPE:
            continue
        phrase = normalize(fact.fact_value.get("phrase", ""))
        if not phrase:
            continue
        by_phrase[phrase].append(fact)

    promotable: list[PromotableAlias] = []
    ambiguous: list[AmbiguousAlias] = []

    for phrase, phrase_facts in sorted(by_phrase.items()):
        slug_counts: dict[str, int] = defaultdict(int)
        slug_type: dict[str, str] = {}
        slug_conf: dict[str, float] = defaultdict(float)
        slug_ids: dict[str, list[int]] = defaultdict(list)
        for fact in phrase_facts:
            slug_counts[fact.subject_id] += fact.observed_count
            slug_type[fact.subject_id] = fact.subject_type
            slug_conf[fact.subject_id] = max(slug_conf[fact.subject_id], fact.confidence)
            slug_ids[fact.subject_id].append(fact.id)

        total = sum(slug_counts.values())
        dominant = max(slug_counts, key=lambda s: slug_counts[s])
        consistency = slug_counts[dominant] / total if total else 0.0

        if len(slug_counts) > 1 and consistency < min_consistency:
            ambiguous.append(AmbiguousAlias(phrase=phrase, slug_counts=dict(slug_counts)))
            continue

        # Согласованная фраза — применяем обычный порог наблюдений/уверенности.
        if slug_counts[dominant] >= min_observations and slug_conf[dominant] >= min_confidence:
            promotable.append(
                PromotableAlias(
                    phrase=phrase,
                    subject_type=slug_type[dominant],
                    slug=dominant,
                    observed_count=slug_counts[dominant],
                    confidence=slug_conf[dominant],
                    fact_ids=slug_ids[dominant],
                )
            )

    return promotable, ambiguous


async def promote_aliases(pool: asyncpg.Pool, facts: list[StructuredFact]) -> AliasPromotionReport:
    """Промоутить согласованные алиасы в aliases справочников опций.

    Мёрж безопасный (как промпт 20): существующие кураторские алиасы не
    затираются, новая фраза добавляется в конец списка (стабильно,
    идемпотентно). Неоднозначные фразы возвращаются в отчёте, а не отбрасываются.
    """
    from app.config import get_settings

    settings = get_settings()
    promotable, ambiguous = analyze_option_aliases(
        facts,
        settings.AI_PROMOTION_MIN_OBSERVATIONS,
        settings.AI_PROMOTION_MIN_CONFIDENCE,
        settings.AI_ALIAS_PROMOTION_MIN_CONSISTENCY,
    )

    if not promotable:
        return AliasPromotionReport(promoted=[], ambiguous=ambiguous)

    # Ленивая загрузка/запись затронутых файлов.
    loaded: dict[str, list] = {}
    dirty: set[str] = set()
    promoted: list[tuple[str, str]] = []
    promoted_fact_ids: list[int] = []

    for item in promotable:
        filename = ALIAS_FILES.get(item.subject_type)
        if filename is None:
            continue
        if filename not in loaded:
            loaded[filename] = load_existing(DATA_DIR / filename)
        entries = loaded[filename]

        idx = next((i for i, e in enumerate(entries) if e.slug == item.slug), None)
        if idx is None:
            continue
        entry = entries[idx]

        existing = {normalize(a) for a in entry.aliases} | {normalize(entry.name)}
        if item.phrase not in existing:
            entries[idx] = entry.model_copy(update={"aliases": (*entry.aliases, item.phrase)})
            dirty.add(filename)

        promoted.append((item.phrase, item.slug))
        promoted_fact_ids.extend(item.fact_ids)

    for filename in dirty:
        write_entries(DATA_DIR / filename, loaded[filename])

    if promoted_fact_ids:
        await pool.execute(
            "UPDATE ai_structured_facts SET source = 'promoted' WHERE id = ANY($1)",
            promoted_fact_ids,
        )

    return AliasPromotionReport(promoted=promoted, ambiguous=ambiguous)


async def find_alias_facts(pool: asyncpg.Pool) -> list[StructuredFact]:
    """Загрузить все наблюдения алиасов (кроме уже промоутированных)."""
    query = """
        SELECT id, subject_type, subject_id, fact_type, fact_value, source, confidence,
               observed_count, first_seen_at, last_confirmed_at
        FROM ai_structured_facts
        WHERE fact_type = $1 AND source != 'promoted'
    """
    rows = await pool.fetch(query, OPTION_ALIAS_FACT_TYPE)
    return [
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
        for row in rows
    ]


async def run_promotion(report_only: bool = False):
    pool = await asyncpg.create_pool(get_settings().DATABASE_URL)
    if not pool:
        logger.error("Failed to connect to database")
        return

    try:
        alias_facts = await find_alias_facts(pool)
        if report_only:
            _, ambiguous = analyze_option_aliases(
                alias_facts,
                _min_obs(),
                _min_conf(),
                _min_consistency(),
            )
            _print_ambiguous(ambiguous)
            return

        facts = await find_promotable_facts(pool)
        report = await promote(pool, facts)
        logger.info(f"Promoted {report.promoted_count} facts, ignored {report.ignored_count}")
        print(f"Promoted: {report.promoted_count}, Ignored: {report.ignored_count}")

        alias_report = await promote_aliases(pool, alias_facts)
        print(f"Promoted aliases: {len(alias_report.promoted)}")
        for phrase, slug in alias_report.promoted:
            print(f"  «{phrase}» → {slug}")
        _print_ambiguous(alias_report.ambiguous)
    finally:
        await pool.close()


def _min_obs() -> int:
    from app.config import get_settings

    return get_settings().AI_PROMOTION_MIN_OBSERVATIONS


def _min_conf() -> float:
    from app.config import get_settings

    return get_settings().AI_PROMOTION_MIN_CONFIDENCE


def _min_consistency() -> float:
    from app.config import get_settings

    return get_settings().AI_ALIAS_PROMOTION_MIN_CONSISTENCY


def _print_ambiguous(ambiguous: list[AmbiguousAlias]) -> None:
    """Отчёт «неоднозначные фразы, требуют ручного решения» (не тихий лог)."""
    if not ambiguous:
        return
    print(
        f"\nНеоднозначные фразы ({len(ambiguous)}) — требуют ручного решения "
        "оператора (не промоучены):"
    )
    for item in ambiguous:
        variants = ", ".join(f"{slug}×{count}" for slug, count in sorted(item.slug_counts.items()))
        print(f"  «{item.phrase}»: {variants}")


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Промоушен ИИ-фактов в справочники.")
    parser.add_argument(
        "--report",
        action="store_true",
        help="Только показать отчёт по неоднозначным фразам-алиасам, ничего не промоутить.",
    )
    args = parser.parse_args()
    asyncio.run(run_promotion(report_only=args.report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
