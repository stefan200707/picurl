import logging

from pydantic import BaseModel

from app.ai.client import call_model
from app.ai.embeddings import embed
from app.ai.memory import lookup_semantic, store_semantic, store_structured_fact
from app.ai.prompts import SYSTEM_PROMPT, build_context
from app.ai.schema import AIEnrichmentAnswer, ComplexCandidate
from app.config import get_settings
from app.geo.candidates import (
    build_candidate_shortlist,
    build_query_signature,
    fully_resolved,
    resolve_known_facts,
)
from app.parsing.schema import Criteria

logger = logging.getLogger(__name__)


class AIMeta(BaseModel):
    ai_used: bool = False
    cache_hit: bool = False
    explanation: str | None = None


class EnrichmentResult(BaseModel):
    ai_used: bool
    cache_hit: bool
    success: bool
    matched_complex_ids: list[str] = []
    center_district_ids: list[str] = []
    poi_findings: dict[str, dict[str, bool]] = {}
    explanation: str | None = None

    @property
    def meta(self) -> AIMeta:
        return AIMeta(
            ai_used=self.ai_used, cache_hit=self.cache_hit, explanation=self.explanation
        )

    @classmethod
    def noop(cls):
        return cls(ai_used=False, cache_hit=False, success=True)

    @classmethod
    def from_deterministic(cls, known: dict):
        return cls(
            ai_used=False,
            cache_hit=False,
            success=True,
            matched_complex_ids=known.get("matched_complex_ids", []),
            center_district_ids=known.get("center_district_ids", []),
            poi_findings=known.get("poi_findings", {}),
        )

    @classmethod
    def from_cache(cls, cached):
        return cls(
            ai_used=False,
            cache_hit=True,
            success=True,
            matched_complex_ids=cached.answer.get("matched_complex_ids", []),
            center_district_ids=cached.answer.get("center_district_ids", []),
            poi_findings=cached.answer.get("poi_findings", {}),
        )

    @classmethod
    def disabled(cls):
        return cls(ai_used=False, cache_hit=False, success=False)

    @classmethod
    def failed(cls):
        return cls(ai_used=True, cache_hit=False, success=False)

    @classmethod
    def from_ai(cls, answer: AIEnrichmentAnswer):
        return cls(
            ai_used=True,
            cache_hit=False,
            success=True,
            matched_complex_ids=answer.matched_complex_ids,
            center_district_ids=answer.center_district_ids,
            poi_findings=answer.poi_findings,
            explanation=answer.explanation,
        )


def merge_enrichment(criteria: Criteria, enrichment: EnrichmentResult) -> Criteria:
    if enrichment.matched_complex_ids:
        from app.parsing.schema import MatchedEntity
        from app.reference.loader import load_complexes

        complexes_data = load_complexes()

        new_complexes = []
        for cid in enrichment.matched_complex_ids:
            for entry in complexes_data:
                if entry.id == cid:
                    new_complexes.append(
                        MatchedEntity(name=entry.name, slug=entry.slug, id=entry.id)
                    )
                    break
        criteria.complexes = new_complexes
    return criteria


def sanitize_against_shortlist(
    answer: AIEnrichmentAnswer, candidates: list[ComplexCandidate]
) -> AIEnrichmentAnswer:
    candidate_ids = {c.id for c in candidates}

    # Filter matched_complex_ids
    answer.matched_complex_ids = [cid for cid in answer.matched_complex_ids if cid in candidate_ids]

    # Filter poi_findings
    answer.poi_findings = {
        cid: findings for cid, findings in answer.poi_findings.items() if cid in candidate_ids
    }

    return answer


async def persist(answer: AIEnrichmentAnswer, signature: str, raw_question: str):
    # Persist structured facts
    for cid in answer.center_district_ids:
        # Simplistic approach for district centering
        await store_structured_fact(
            "district", cid, "is_center", {"is_center": True}, "ai_inference", answer.confidence
        )

    for cid, findings in answer.poi_findings.items():
        for poi_category, is_present in findings.items():
            await store_structured_fact(
                "complex",
                cid,
                f"poi_{poi_category}",
                {"present": is_present},
                "ai_inference",
                answer.confidence,
            )

    # Persist semantic cache
    embedding = embed(signature)
    answer_dict = {
        "matched_complex_ids": answer.matched_complex_ids,
        "center_district_ids": answer.center_district_ids,
        "poi_findings": answer.poi_findings,
    }
    await store_semantic(signature, embedding, raw_question, answer_dict)


async def enrich(text: str, criteria: Criteria, warnings: list[str]) -> EnrichmentResult:
    if not criteria.poi_requirements and not criteria.center_requested:
        return EnrichmentResult.noop()

    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    if fully_resolved(known, criteria):
        return EnrichmentResult.from_deterministic(known)

    signature = build_query_signature(text, criteria)
    embedding = embed(signature)

    try:
        cached = await lookup_semantic(signature, embedding)
    except Exception as e:
        logger.warning(f"Failed to lookup semantic cache: {e}")
        cached = None

    if cached is not None:
        return EnrichmentResult.from_cache(cached)

    settings = get_settings()
    if not settings.AI_ENRICHMENT_ENABLED or not settings.ANTHROPIC_API_KEY:
        warnings.append("ИИ-обогащение выключено — часть запроса не обработана")
        return EnrichmentResult.disabled()

    try:
        context = build_context(text, criteria, candidates, known)
        answer = await call_model(SYSTEM_PROMPT, context)
    except (ValueError, Exception) as e:
        from anthropic import APIStatusError, APITimeoutError
        from pydantic import ValidationError
        if isinstance(e, (APIStatusError, APITimeoutError, ValueError, ValidationError)):
            logger.error(f"AI enrichment failed: {e}", exc_info=True)
            warnings.append("не удалось обработать ИИ-обогащение (ошибка сервиса)")
            return EnrichmentResult.failed()
        raise e

    answer = sanitize_against_shortlist(answer, candidates)

    try:
        await persist(answer, signature, text)
    except Exception as e:
        logger.warning(f"Failed to persist AI results to DB: {e}")

    return EnrichmentResult.from_ai(answer)
