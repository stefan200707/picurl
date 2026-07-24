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


FREE_TEXT_SYSTEM_PROMPT = """Ты извлекаешь СКАЛЯРНЫЕ фильтры квартиры из свободного
русского текста. Тебе дан запрос пользователя (user_query), уже распознанные
детерминированным парсером фильтры (already_parsed) и список фрагментов, которые
парсер НЕ смог разобрать (unresolved_fragments).

Твоя задача — достать из текста ТОЛЬКО те поля ниже, которых НЕТ в already_parsed
(парсер уже победил — не дублируй и не переопределяй его):
- rooms: число комнат — "studio" | "one" | "two" | "three_plus" (список).
- price_min / price_max: цена в рублях (целое; "до 15 млн" → price_max=15000000).
- area_min / area_max: общая площадь, м².
- area_kitchen_min / area_kitchen_max: площадь кухни, м².
- floor_min / floor_max: этаж.
- not_first_floor / last_floor / not_last_floor: булевы пожелания по этажу.
- ready: готов к заселению (true), если явно сказано.
- sort: сортировка — "price_asc" | "price_desc" | "area_asc" | "area_desc".
- housing_type: "flats_only" (только квартиры, без апартаментов) | "any".
- settlement_year_from / settlement_year_to: год заселения/сдачи.
- time_on_foot: время до метро ПЕШКОМ, минуты («метро в шаговой доступности 10
  минут» → 10). time_on_transport: время до метро на транспорте/машине, минуты.
- only_available: показывать только свободные/не забронированные (true).

СТРОГИЕ ПРАВИЛА:
1. НИКОГДА не извлекай метро, районы, округа, ЖК, названия ориентиров, опции
   (санузлы, окна, отделка и т.п.) — этим занимаются другие слои. Если фрагмент
   про них — просто пропусти его.
2. Ничего не выдумывай. Если поля в тексте нет или ты не уверен — оставь его
   пустым/null. Лучше пропустить, чем угадать.
3. Заполняй поле, только если его нет в already_parsed.
4. В consumed_fragments перечисли те строки из unresolved_fragments (дословно),
   которые ты реально сопоставил с каким-то полем выше.

Пример: user_query="однушка падешевле, не первый этаж", already_parsed уже
содержит rooms=["one"], unresolved_fragments=["падешевле", "не первый этаж"].
Ответ: sort="price_asc", not_first_floor=true,
consumed_fragments=["падешевле", "не первый этаж"]. rooms НЕ трогаем (уже есть).
"""


def build_free_text_context(text: str, criteria: Criteria, fragments: Sequence[str]) -> dict:
    """Контекст для извлечения недостающих скаляров из свободного текста.

    Отдаём модели полный текст (для контекста), уже распознанные фильтры
    (``already_parsed`` — чтобы не дублировала детерминированный слой) и
    неразобранные фрагменты (``unresolved_fragments`` — подсказка, что осталось).
    """
    return {
        "user_query": text,
        "already_parsed": criteria.to_public_dict(),
        "unresolved_fragments": list(fragments),
    }
