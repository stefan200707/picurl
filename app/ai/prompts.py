from app.ai.schema import ComplexCandidate
from app.parsing.schema import Criteria

SYSTEM_PROMPT = """Тебе даны кандидаты ЖК с известными фактами
(район/округ/метро/признак центра/POI).
Реши, какие из НИХ подходят под запрос пользователя.
Никогда не упоминай ЖК, которых нет в списке кандидатов.
Если факт неизвестен (null) — не утверждай его наличие или отсутствие,
оставь соответствующий ключ вне poi_findings
или явно отметь как неизвестный, в зависимости от финальной схемы ответа.

Пример:
Пользователь ищет: "нужна квартира трёшка в центре рядом продуктовые и детсады и школы"
Если передан ЖК "Олимпия" (is_center=True, known_poi={"school": True, "shop": True,
"kindergarten": False}),
тогда он не подходит, т.к. kindergarten=False.
"""


def build_context(
    text: str, criteria: Criteria, candidates: list[ComplexCandidate], known_facts: dict
) -> dict:
    return {
        "user_query": text,
        "criteria": criteria.to_public_dict(),
        "candidates": [c.model_dump() for c in candidates],
        "known_facts": known_facts,
    }
