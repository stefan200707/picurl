from collections.abc import Sequence

from app.ai.schema import ComplexCandidate
from app.parsing.schema import Criteria
from app.reference.loader import RefEntry

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
    # Модели отдаём только объективные факты (POI кандидатов), но не готовое
    # решение (matched_complex_ids / center_district_ids) — иначе она склонна
    # слепо копировать «подсказку» вместо самостоятельного отбора.
    return {
        "user_query": text,
        "criteria": criteria.to_public_dict(),
        "candidates": [c.model_dump() for c in candidates],
        "known_poi": known_facts.get("poi_findings", {}),
    }


OPTION_SYSTEM_PROMPT = """Тебе даны фрагменты запроса пользователя, которые
детерминированный парсер не смог сопоставить ни с одним фильтром pik.ru, и полный
список доступных фильтров-опций (options) и групп опций (option_groups) с их
именами и slug'ами.

Для КАЖДОГО фрагмента реши, означает ли он по смыслу какую-то из перечисленных
опций/групп опций. Если да — верни её slug ровно как в списке. Если фрагмент не
соответствует ни одной опции — верни slug=null. Ничего не выдумывай: slug должен
быть строго из предоставленного списка, иначе null. Не пытайся заменить фильтр,
которого нет в списке, на «похожий» — лучше null.

Пример: фрагмент «квартира с отдельным санузлом на каждую спальню» по смыслу
близок к «Два и более санузла» (slug=manybathrooms) — верни этот slug. Фрагмент
«с видом на закат» ни одной опции не соответствует — верни null.
"""


def build_option_context(
    fragments: Sequence[str],
    options: Sequence[RefEntry],
    option_groups: Sequence[RefEntry],
) -> dict:
    """Контекст для резолвинга фраз под опции.

    В отличие от обогащения ЖК, кандидаты здесь — сами фильтры, поэтому в модель
    уходит ПОЛНЫЙ список options/option_groups (name + slug), без усечения
    шорт-листом, плюс сами нераспознанные фрагменты.
    """
    return {
        "fragments": list(fragments),
        "options": [{"name": e.name, "slug": e.slug} for e in options if e.slug],
        "option_groups": [{"name": e.name, "slug": e.slug} for e in option_groups if e.slug],
    }
