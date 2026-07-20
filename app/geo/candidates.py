from app.ai.schema import ComplexCandidate
from app.parsing.schema import Criteria
from app.reference.loader import load_all


def build_candidate_shortlist(criteria: Criteria) -> list[ComplexCandidate]:
    ref_data = load_all()
    # Simple heuristic: take complexes from criteria if specified, or a short list.
    candidates = []

    # Restrict to specified complexes if any
    allowed_ids = {c.id for c in criteria.complexes if c.id}

    for c in ref_data.complexes:
        if allowed_ids and c.id not in allowed_ids:
            continue

        candidates.append(ComplexCandidate(
            id=c.id or "",
            name=c.name,
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={}
        ))

        # In a real geo-layer, this would check distance, POIs from memory, etc.
        # But for this prompt, we just return basic candidate info.
        # Limit to 20 to avoid huge prompts
        if len(candidates) >= 20:
            break

    return candidates

def resolve_known_facts(candidates: list[ComplexCandidate], criteria: Criteria) -> dict:
    return {
        "matched_complex_ids": [c.id for c in candidates],
        "center_district_ids": [],
        "poi_findings": {}
    }

def fully_resolved(known: dict, criteria: Criteria) -> bool:
    # Always fallback to AI for semantic parts in this simplified version
    return False

def build_query_signature(text: str, criteria: Criteria) -> str:
    # Normalize text + criteria for semantic caching
    return text.lower().strip()
