from app.ai.schema import ComplexCandidate
from app.parsing.schema import Criteria
from app.reference.loader import load_all, DATA_DIR
import json


def build_candidate_shortlist(criteria: Criteria) -> list[ComplexCandidate]:
    ref_data = load_all()
    poi_cache_path = DATA_DIR / "poi_cache.json"
    poi_cache = json.loads(poi_cache_path.read_text("utf-8")) if poi_cache_path.exists() else {}
    
    candidates = []
    allowed_ids = {c.id for c in criteria.complexes if c.id}

    for c in ref_data.complexes:
        if allowed_ids and c.id not in allowed_ids:
            continue
            
        known_poi = {}
        if c.slug and c.slug in poi_cache:
            for cat, data in poi_cache[c.slug].items():
                known_poi[cat] = data.get("count", 0) > 0

        candidates.append(
            ComplexCandidate(
                id=c.id or "",
                name=c.name,
                district=None,
                county=None,
                metro=[],
                is_center=None,
                known_poi=known_poi,
            )
        )

        if len(candidates) >= 20:
            break

    return candidates


def resolve_known_facts(candidates: list[ComplexCandidate], criteria: Criteria) -> dict:
    ref_data = load_all()
    center_district_ids = [d.id for d in ref_data.districts if d.is_center and d.id]
    
    poi_findings = {}
    matched_complex_ids = []
    
    for c in candidates:
        poi_findings[c.id] = dict(c.known_poi)
        
        satisfies = True
        if criteria.poi_requirements:
            for req in criteria.poi_requirements:
                if req.category.value not in c.known_poi or c.known_poi[req.category.value] is not True:
                    satisfies = False
                    break
                    
        if criteria.center_requested:
            satisfies = False
            
        if satisfies:
            matched_complex_ids.append(c.id)

    return {
        "matched_complex_ids": matched_complex_ids,
        "center_district_ids": center_district_ids,
        "poi_findings": poi_findings,
    }


def fully_resolved(known: dict, criteria: Criteria) -> bool:
    if criteria.center_requested:
        return False
        
    if criteria.poi_requirements:
        for cid, findings in known.get("poi_findings", {}).items():
            for req in criteria.poi_requirements:
                if req.category.value not in findings:
                    return False
    return True

def build_query_signature(text: str, criteria: Criteria) -> str:
    return text.lower().strip()
