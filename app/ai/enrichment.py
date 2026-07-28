import logging

import asyncpg
from pydantic import BaseModel

from app.ai.client import (
    CircuitOpenError,
    call_free_text_extractor,
    call_model,
    call_option_resolver,
    claude_credentials_available,
)
from app.ai.embeddings import embed
from app.ai.memory import (
    log_ai_call,
    lookup_semantic,
    store_semantic,
    store_structured_fact,
)
from app.ai.prompts import (
    FREE_TEXT_SYSTEM_PROMPT,
    OPTION_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_context,
    build_free_text_context,
    build_option_context,
)
from app.ai.schema import (
    AIEnrichmentAnswer,
    ComplexCandidate,
    FreeTextCriteriaAnswer,
    OptionResolutionAnswer,
)
from app.config import get_settings
from app.geo.candidates import (
    build_candidate_shortlist,
    build_query_signature,
    fully_resolved,
    landmark_nearest,
    landmark_nearest_fallback,
    resolve_known_facts,
    station_class_nearest_fallback,
    station_class_points,
)
from app.geo.poi import POI_CACHE_SCHEMA_VERSION
from app.parsing.rules.landmark import has_superlative_cue
from app.parsing.schema import Criteria, LandmarkRequirement
from app.reference.loader import load_landmarks, load_option_groups, load_options, normalize

#: fact_type для логирования сопоставлений «фраза → slug фильтра» в
#: ai_structured_facts (переиспользуем карту памяти промпта 17). Промоушен этих
#: наблюдений в aliases справочников делает app/ai/promotion.py.
OPTION_ALIAS_FACT_TYPE = "option_alias"

#: Хвост warning'а «не удалось распознать» (шаблон из parser.py). По нему
#: восстанавливаем реальный остаток текста для экстрактора свободного текста и
#: снимаем warning'и по consumed_fragments (как option-резолвинг по phrase).
_UNRECOGNIZED_SUFFIX = "»: не удалось распознать, не попало в ссылку"

#: Приветствия — остаток без фильтрующего смысла в ЛЮБОМ контексте. В модель не
#: уходят: живой замер показал вызов экстрактора с fragments=1, где единственным
#: фрагментом было «Привет», и вся работа модели свелась к объяснению, что
#: приветствие не относится ни к одному полю Criteria. Так происходит на каждом
#: запросе, начинающемся с приветствия.
#: Из warnings приветствие при этом НЕ убирается (инвариант 1) — «показывать ли
#: пользователю шум» решается отдельно и разом для всей категории noise.
#: Падежей у этих форм нет, поэтому список закрытый; сравнение — по normalize()
#: (casefold, ё→е, схлопывание пробелов) плюс обрезка краевой пунктуации.
_GREETINGS = frozenset(
    {
        "привет",
        "приветик",
        "приветики",
        "привет всем",
        "всем привет",
        "приветствую",
        "приветствую вас",
        "здравствуй",
        "здравствуйте",
        "здрасте",
        "здрасьте",
        "доброе утро",
        "доброго утра",
        "утро доброе",
        "добрый день",
        "доброго дня",
        "день добрый",
        "добрый вечер",
        "доброго вечера",
        "вечер добрый",
        "доброй ночи",
        "доброго времени суток",
        "доброго времени",
        "хай",
    }
)
#: Краевая пунктуация фрагмента. parser.py часть её уже снимает при нарезке
#: остатка, но `_residual_fragments` разбирает warning'и, а не текст, — свою
#: обрезку он не наследует.
_GREETING_STRIP_CHARS = " ,.!?…:;-–—()"

#: Скалярные поля Criteria, которые экстрактор свободного текста вправе заполнять
#: (None = «не задано»). БЕЗ метро/районов/округов/ЖК/опций — те требуют
#: резолвинга справочника и остаются на детерминированных путях + sanitize_*.
_FREE_TEXT_SCALAR_FIELDS = (
    "price_min",
    "price_max",
    "area_min",
    "area_max",
    "area_kitchen_min",
    "area_kitchen_max",
    "floor_min",
    "floor_max",
    "ready",
    "sort",
    "housing_type",
    "settlement_year_from",
    "settlement_year_to",
    "time_on_foot",
    "time_on_transport",
)
#: Булевы пожелания (дефолт False = «не задано»): включаем только True поверх False.
_FREE_TEXT_BOOL_FIELDS = ("not_first_floor", "last_floor", "not_last_floor", "only_available")

logger = logging.getLogger(__name__)


class FreeTextOutcome(BaseModel):
    """Итог работы экстрактора свободного текста (ведро C).

    ``called`` — попытка реально дошла до модели (для ai_call_log). ``changed`` —
    модель заполнила хоть одно пустое поле criteria (для ai_used/criteria_changed).
    """

    called: bool = False
    changed: bool = False
    explanation: str = ""


class AIMeta(BaseModel):
    ai_used: bool = False
    ai_failed: bool = False
    cache_hit: bool = False
    explanation: str | None = None


class EnrichmentResult(BaseModel):
    """Итог обогащения одного запроса.

    ``ai_used`` — честный флаг «ИИ реально повлиял на результат» (успешный
    живой вызов модели или landmark-сужение, см. ниже); он НЕ означает «была
    попытка обратиться к ИИ». Неудачную попытку (сеть/валидация/провайдер
    упал) фиксирует отдельное поле ``ai_failed`` — до этой правки ``failed()``
    выставлял ``ai_used=True`` при провале, из-за чего ответ API читался как
    «ИИ поучаствовал», хотя он упал с ошибкой и ничего не вернул.
    """

    ai_used: bool
    ai_failed: bool = False
    cache_hit: bool
    success: bool
    matched_complex_ids: list[str] = []
    #: True, когда ``matched_complex_ids`` — результат РЕАЛЬНОГО сужения
    #: (детерминированного/кэшированного/ИИ), а не значение по умолчанию.
    #: Различает «ЖК не считали вовсе» (``noop()``/``failed()``/``disabled()``,
    #: matched_complex_ids=[] по конструктору) от «считали и получили пустой
    #: список» (легитимный ноль — ориентир/POI/центр реально не оставили ни
    #: одного ЖК). ``merge_enrichment`` читает этот флаг, чтобы не спутать два
    #: состояния и корректно занулить ``criteria.complexes`` во втором случае
    #: (см. ``Criteria.complexes_matched_empty``).
    complexes_matched: bool = False
    center_district_ids: list[str] = []
    poi_findings: dict[str, dict[str, bool]] = {}
    explanation: str | None = None

    @property
    def meta(self) -> AIMeta:
        return AIMeta(
            ai_used=self.ai_used,
            ai_failed=self.ai_failed,
            cache_hit=self.cache_hit,
            explanation=self.explanation,
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
            complexes_matched=True,
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
            complexes_matched=True,
            center_district_ids=cached.answer.get("center_district_ids", []),
            poi_findings=cached.answer.get("poi_findings", {}),
        )

    @classmethod
    def disabled(cls):
        return cls(ai_used=False, cache_hit=False, success=False)

    @classmethod
    def failed(cls):
        # ai_used=False: попытка провалилась, ИИ ни на что не повлиял.
        # ai_failed=True: сам факт неудачной попытки не теряется молча.
        return cls(ai_used=False, ai_failed=True, cache_hit=False, success=False)

    @classmethod
    def from_ai(cls, answer: AIEnrichmentAnswer):
        return cls(
            ai_used=True,
            cache_hit=False,
            success=True,
            matched_complex_ids=answer.matched_complex_ids,
            complexes_matched=True,
            center_district_ids=answer.center_district_ids,
            poi_findings=answer.poi_findings,
            explanation=answer.explanation,
        )


def merge_enrichment(criteria: Criteria, enrichment: EnrichmentResult) -> Criteria:
    # Импорт поднят один раз в начало функции (AUDIT_REPORT 2.6): раньше
    # MatchedEntity импортировался дважды в двух разных if-ветках.
    from app.parsing.schema import MatchedEntity

    # ``complexes_matched`` (не truthy-проверка matched_complex_ids!) отличает
    # «сужение реально считалось» от «matched_complex_ids=[] по умолчанию,
    # ничего не считали» — truthy-проверка их не различала, и легитимный ноль
    # (ориентир/POI/центр реально не оставили ни одного ЖК) был неотличим от
    # «ЖК не выбирались вовсе»: criteria.complexes оставался нетронутым, и
    # downstream (build_url/validate) не видел, что locations-фильтр вообще
    # участвовал (Дефект №1 — тихая потеря при AND с гео-фолбэком МКАД).
    if enrichment.complexes_matched:
        from app.reference.loader import load_complexes

        complexes_data = load_complexes()

        new_complexes = []
        seen_ids: set[str] = set()
        # dict.fromkeys — дедуп id с сохранением порядка (дубль id в шорт-листе
        # раньше давал один и тот же ЖК дважды и в criteria, и в blocks= URL).
        for cid in dict.fromkeys(enrichment.matched_complex_ids):
            for entry in complexes_data:
                if entry.id == cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    new_complexes.append(
                        MatchedEntity(name=entry.name, slug=entry.slug, id=entry.id)
                    )
                    break
        criteria.complexes = new_complexes
        # Явный ноль (не «не считали») — сигнал для geo-фолбэка (МКАД и т.п.):
        # пересечение с этим требованием должно давать пусто, а не тихо
        # игнорировать его и откатываться на весь фолбэк-список.
        criteria.complexes_matched_empty = not new_complexes

    if enrichment.center_district_ids:
        from app.reference.loader import load_districts

        districts_data = load_districts()

        new_districts = []
        for did in enrichment.center_district_ids:
            for entry in districts_data:
                if entry.id == did:
                    new_districts.append(
                        MatchedEntity(name=entry.name, slug=entry.slug, id=entry.id)
                    )
                    break
        criteria.districts.extend(new_districts)

    return criteria


def sanitize_against_shortlist(
    answer: AIEnrichmentAnswer, candidates: list[ComplexCandidate]
) -> AIEnrichmentAnswer:
    candidate_ids = {c.id for c in candidates}
    candidate_district_ids = {c.district for c in candidates if c.district}

    # Filter matched_complex_ids
    answer.matched_complex_ids = [cid for cid in answer.matched_complex_ids if cid in candidate_ids]

    # Filter center_district_ids
    answer.center_district_ids = [
        did for did in answer.center_district_ids if did in candidate_district_ids
    ]

    # Filter poi_findings
    answer.poi_findings = {
        cid: findings for cid, findings in answer.poi_findings.items() if cid in candidate_ids
    }

    return answer


async def persist(
    answer: AIEnrichmentAnswer, signature: str, raw_question: str, pool: asyncpg.Pool | None
):
    if pool is None:
        return
    # Persist structured facts
    for cid in answer.center_district_ids:
        # Simplistic approach for district centering
        await store_structured_fact(
            pool,
            "district",
            cid,
            "is_center",
            {"is_center": True},
            "ai_inference",
            answer.confidence,
        )

    for cid, findings in answer.poi_findings.items():
        for poi_category, is_present in findings.items():
            await store_structured_fact(
                pool,
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
    await store_semantic(pool, signature, embedding, raw_question, answer_dict)


def sanitize_option_resolution(
    answer: OptionResolutionAnswer,
) -> list[tuple[str, str, str, float]]:
    """Валидировать ответ модели против реального списка slug'ов справочника.

    Аналог :func:`sanitize_against_shortlist` для опций: строке из ответа модели
    не доверяем слепо. Возвращает список ``(phrase, slug, subject_type,
    confidence)`` только для slug'ов, реально существующих в
    ``options.json``/``option_groups.json``; выдуманные slug и ``null`` отсекаются.
    ``subject_type`` — ``"option"`` или ``"option_group"`` (определяется по тому,
    в каком справочнике найден slug).
    """
    option_slugs = {e.slug for e in load_options() if e.slug}
    group_slugs = {e.slug for e in load_option_groups() if e.slug}

    resolved: list[tuple[str, str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    for match in answer.matches:
        if not match.slug:
            continue
        if match.slug in group_slugs:
            subject_type = "option_group"
        elif match.slug in option_slugs:
            subject_type = "option"
        else:
            # Галлюцинация: slug вне справочника — не доверяем.
            continue
        key = (normalize(match.phrase), match.slug)
        if key in seen:
            continue
        seen.add(key)
        resolved.append((match.phrase, match.slug, subject_type, match.confidence))
    return resolved


def sanitize_landmark_resolution(
    answer: FreeTextCriteriaAnswer,
    fragments: list[str],
    nearest_only: bool = False,
) -> list[LandmarkRequirement]:
    """Превратить ответ модели по ориентирам в требования, взяв координаты из
    ``landmarks.json``.

    Тот же рубеж, что :func:`sanitize_option_resolution`: доверяем не строке
    модели, а справочнику. Slug вне справочника — галлюцинация, отбрасывается
    (фраза остаётся в ``warnings``). Записи без ``lat``/``lon`` пропускаем: без
    координат гео-сужение физически невозможно (см.
    :mod:`app.parsing.rules.landmark`), а выдумывать точку нельзя — инвариант
    «LLM не считает дистанции».

    ``phrase`` тоже проверяется — она обязана быть ОДНИМ ИЗ переданных модели
    ``fragments``. Промпт этого требует, но требование без проверки ничего не
    стоит: реальный slug, привязанный к произвольной фразе, иначе прошёл бы
    насквозь и на любом шуме в остатке молча сузил бы выдачу по случайному
    ориентиру.

    ``nearest_only`` прокидывается из текста запроса
    (:func:`app.parsing.rules.landmark.has_superlative_cue`): «самую ближайшую»
    остаётся суперлативом, даже когда ориентир достал ИИ, а не regex.
    """
    by_slug = {e.slug: e for e in load_landmarks() if e.slug}
    allowed_phrases = set(fragments)

    requirements: list[LandmarkRequirement] = []
    seen: set[str] = set()
    for match in answer.landmarks:
        if not match.slug or match.phrase not in allowed_phrases:
            continue
        entry = by_slug.get(match.slug)
        if entry is None or entry.lat is None or entry.lon is None:
            continue
        if entry.name in seen:
            continue
        seen.add(entry.name)
        requirements.append(
            LandmarkRequirement(
                name=entry.name,
                lat=entry.lat,
                lon=entry.lon,
                category=entry.category,
                raw_phrase=match.phrase,
                nearest_only=nearest_only,
            )
        )
    return requirements


async def resolve_options(
    criteria: Criteria,
    option_candidates: list[str],
    warnings: list[str],
    pool: asyncpg.Pool | None,
) -> None:
    """ИИ-резолвинг нераспознанных фраз под опции/группы опций.

    Новая способность (Milestone AI-10): rapidfuzz матчит фильтры по строковому
    сходству, поэтому фразы-синонимы («отдельный санузел» ≈ «Два и более
    санузла») до сих пор молча уходили в warnings. Здесь короткие нераспознанные
    фрагменты (собранные фасадом parse) уходят в модель вместе с полным списком
    опций; подтверждённые (и провалидированные против справочника) slug'и
    применяются к ``criteria`` так же, как если бы их сматчил rapidfuzz, и
    убираются из warnings. Каждое сопоставление логируется в карту памяти для
    последующего промоушена в aliases (см. app/ai/promotion.py).
    """
    if not option_candidates:
        return

    settings = get_settings()
    # .lower() — как в client.call_typed: AI_PROVIDER=Claude не должен
    # проскакивать гейт и падать уже внутри клиента. Учётными данными
    # считается и API-ключ, и OAuth-сессия Claude Code
    # (см. app.ai.client.claude_credentials_available).
    is_claude_missing = settings.AI_PROVIDER.lower() == "claude" and not (
        claude_credentials_available(settings)
    )
    if not settings.AI_ENRICHMENT_ENABLED or is_claude_missing:
        # ИИ выключен — фрагменты остаются в warnings как есть, ничего не теряем.
        return

    context = build_option_context(option_candidates, load_options(), load_option_groups())
    try:
        answer = await call_option_resolver(OPTION_SYSTEM_PROMPT, context)
    except CircuitOpenError as e:
        # Circuit breaker открыт — это ожидаемое временное состояние (часть B),
        # а не баг: логируем мягче и без трейсбека. Фразы уже несут предметный
        # warning («…: не удалось распознать, не попало в ссылку»), отдельный
        # текст здесь не нужен — сохранять как есть достаточно.
        logger.warning(f"AI option resolution skipped (circuit breaker open): {e}")
        return
    except Exception as e:
        # Модель могла упасть (сеть/валидация) — деградируем мягко: фрагменты
        # остаются в warnings, ничего не выдумываем.
        logger.error(f"AI option resolution failed: {e}", exc_info=True)
        return

    for phrase, slug, subject_type, confidence in sanitize_option_resolution(answer):
        if subject_type == "option_group":
            if slug not in criteria.option_groups:
                criteria.option_groups.append(slug)
        else:
            if slug not in criteria.options:
                criteria.options.append(slug)

        # Фраза распознана — убираем её из «не удалось распознать».
        stale = f"«{phrase}»: не удалось распознать, не попало в ссылку"
        if stale in warnings:
            warnings.remove(stale)

        # Логируем сопоставление «нормализованная фраза → slug» для промоушена.
        if pool is not None:
            try:
                await store_structured_fact(
                    pool,
                    subject_type,
                    slug,
                    OPTION_ALIAS_FACT_TYPE,
                    {"phrase": normalize(phrase)},
                    "ai_inference",
                    confidence,
                )
            except Exception as e:
                logger.warning(f"Failed to persist option alias: {e}")


def _is_greeting(fragment: str) -> bool:
    """Фрагмент целиком — приветствие (и ничего больше)."""
    return normalize(fragment.strip(_GREETING_STRIP_CHARS)) in _GREETINGS


def _residual_fragments(warnings: list[str]) -> list[str]:
    """Восстановить неразобранные фрагменты из warning'ов «не удалось распознать».

    Реальный остаток (ведро C) отличаем от «не поддерживается pik.ru» (ведро B):
    в ИИ уходит только первое — по фильтрам ведра B строить нечего, вызов был бы
    сожжён впустую (инвариант «в ИИ не летит мусор»). По той же причине
    отбрасываются приветствия (:data:`_GREETINGS`) — из warnings они при этом НЕ
    исчезают (инвариант 1), меняется только состав того, что уходит модели.
    """
    fragments: list[str] = []
    for w in warnings:
        if w.startswith("«") and w.endswith(_UNRECOGNIZED_SUFFIX):
            fragment = w[1 : -len(_UNRECOGNIZED_SUFFIX)]
            if not _is_greeting(fragment):
                fragments.append(fragment)
    return fragments


def _apply_free_text_answer(
    criteria: Criteria,
    answer: FreeTextCriteriaAnswer,
    fragments: list[str],
    warnings: list[str],
    text: str = "",
) -> bool:
    """Применить ответ экстрактора к criteria. Детерминированный слой выигрывает:
    заполняем ТОЛЬКО пустые поля; невалидное значение отбрасывает pydantic
    (validate_assignment у Criteria) — молча не выдумываем. Возвращает, изменилось
    ли criteria."""
    changed = False

    # rooms — только если детерминированный слой ничего не нашёл.
    if answer.rooms and not criteria.rooms:
        try:
            criteria.rooms = list(dict.fromkeys(answer.rooms))
            changed = True
        except Exception as e:
            logger.warning(f"free-text rooms rejected: {e}")

    # Скаляры — заполняем, только если поле не задано (None).
    for field in _FREE_TEXT_SCALAR_FIELDS:
        value = getattr(answer, field)
        if value is not None and getattr(criteria, field) is None:
            try:
                setattr(criteria, field, value)
                changed = True
            except Exception as e:
                logger.warning(f"free-text {field}={value!r} rejected: {e}")

    # Ориентиры — единственное неcкалярное поле, доверенное экстрактору
    # (Milestone AI-22). Как и везде: детерминированный слой выигрывает (пишем
    # только в пустой список), координаты берёт справочник, а не модель.
    accepted_landmark_phrases: set[str] = set()
    if answer.landmarks and not criteria.landmark_requirements:
        resolved_landmarks = sanitize_landmark_resolution(
            answer, fragments, nearest_only=has_superlative_cue(text)
        )
        if resolved_landmarks:
            try:
                criteria.landmark_requirements = resolved_landmarks
                accepted_landmark_phrases = {lm.raw_phrase for lm in resolved_landmarks}
                changed = True
            except Exception as e:
                logger.warning(f"free-text landmarks rejected: {e}")

    # Булевы флаги — включаем только True поверх дефолтного False.
    for field in _FREE_TEXT_BOOL_FIELDS:
        if getattr(answer, field) and not getattr(criteria, field):
            try:
                setattr(criteria, field, True)
                changed = True
            except Exception as e:
                logger.warning(f"free-text {field} rejected: {e}")

    # Снимаем warning'и по фрагментам, которые модель заявила разобранными —
    # но только среди реально переданных ей fragments (не доверяем строке слепо).
    # Фразы ориентиров, отбитых санитайзером, из снятия исключены: иначе хватало
    # модели заодно угадать любое другое поле (changed=True), чтобы выдуманный
    # ориентир исчез молча вместе со своей фразой — прямое нарушение инварианта 1.
    rejected_landmark_phrases = {
        m.phrase for m in answer.landmarks if m.phrase not in accepted_landmark_phrases
    }
    if changed:
        for frag in answer.consumed_fragments:
            if frag in fragments and frag not in rejected_landmark_phrases:
                stale = f"«{frag}{_UNRECOGNIZED_SUFFIX}"
                if stale in warnings:
                    warnings.remove(stale)

    return changed


async def resolve_free_text_criteria(
    criteria: Criteria,
    text: str,
    warnings: list[str],
    pool: asyncpg.Pool | None,
) -> FreeTextOutcome:
    """Новая способность (ведро C): достать недостающие СКАЛЯРНЫЕ фильтры из
    свободного текста через модель, когда детерминированный парсер оставил
    значимый остаток. Независимо от гейтов и до них (мутирует criteria/warnings
    на месте) — это и есть «ослабление гейтов»: запрос без структурных сигналов
    (poi/center/landmark/station), но с непонятым текстом теперь доходит до ИИ.

    Локации/опции модель не трогает (см. FREE_TEXT_SYSTEM_PROMPT); значения
    ограничены схемой, детерминированное всегда выигрывает. Мягкая деградация:
    любой сбой — фрагменты остаются в warnings как есть.
    """
    fragments = _residual_fragments(warnings)
    if not fragments:
        # Нет реального остатка (либо всё разобрано, либо остаток — только
        # «не поддерживается pik.ru»/шум) — модель не зовём.
        return FreeTextOutcome()

    settings = get_settings()
    is_claude_missing = settings.AI_PROVIDER.lower() == "claude" and not (
        claude_credentials_available(settings)
    )
    if not settings.AI_ENRICHMENT_ENABLED or is_claude_missing:
        return FreeTextOutcome()

    context = build_free_text_context(text, criteria, fragments)
    try:
        answer = await call_free_text_extractor(FREE_TEXT_SYSTEM_PROMPT, context)
    except CircuitOpenError as e:
        logger.warning(f"AI free-text extraction skipped (circuit breaker open): {e}")
        return FreeTextOutcome(called=False)
    except Exception as e:
        logger.error(f"AI free-text extraction failed: {e}", exc_info=True)
        return FreeTextOutcome(called=True)

    changed = _apply_free_text_answer(criteria, answer, fragments, warnings, text)
    return FreeTextOutcome(
        called=True,
        changed=changed,
        explanation=answer.explanation if changed else "",
    )


def _describe_unmet_ai_requirements(criteria: Criteria) -> str:
    """Человекочитаемое перечисление того, что осталось необработанным при
    провале ИИ-слоя (часть C: предметная деградация).

    До этой правки провал POI-ветки давал один и тот же обезличенный текст
    («не удалось обработать ИИ-обогащение (ошибка сервиса)») независимо от
    того, что именно просил пользователь — в отличие от landmark/station-class
    веток, которые всегда называют конкретный ориентир/линию. Пользователь не
    мог отличить «садик учтён» от «садик проигнорирован». Собирается из
    ``criteria.poi_requirements`` (категория через исходную фразу пользователя
    ``raw_phrase`` — точнее перевода ``POICategory`` на русский) плюс флага
    ``center_requested``; per-instance детали (``only_new``/``max_distance_m``)
    добавляются в скобках, как и было решено в аудите
    ``POIRequirement.max_distance_m`` (см. CLAUDE.md, «Аудит тихих потерь»).
    """
    parts: list[str] = []
    for req in criteria.poi_requirements:
        detail_bits: list[str] = []
        if req.only_new:
            detail_bits.append("только новые")
        if req.max_distance_m is not None:
            detail_bits.append(f"не дальше {req.max_distance_m} м")
        detail = f" ({', '.join(detail_bits)})" if detail_bits else ""
        parts.append(f"«{req.raw_phrase}»{detail}")
    if criteria.center_requested:
        parts.append("«в центре»")
    return "; ".join(parts) if parts else "запрос"


def _differs_from_deterministic(result: EnrichmentResult, known: dict) -> bool:
    """Отличается ли итог обогащения от того, что дал бы детерминированный слой.

    Используется для колонки ``criteria_changed_by_ai`` в ai_call_log: это
    реальная польза вызова (ИИ реально изменил бы ``criteria``), а не просто
    факт, что вызов состоялся. Сравнение по множествам id — порядок не важен.
    """
    return set(result.matched_complex_ids) != set(known.get("matched_complex_ids", [])) or set(
        result.center_district_ids
    ) != set(known.get("center_district_ids", []))


def _all_superlative(landmarks: list[LandmarkRequirement]) -> bool:
    """Суперлативны ли ВСЕ ориентиры запроса («самую ближайшую к X»).

    Именно «все», а не «хотя бы один»: смешанный запрос («рядом с МГУ и
    ближайшую к Политеху») при `any()` отбрасывал радиусное требование целиком,
    а warning утверждал «ближайшие к обоим», хотя ни один ЖК не может быть
    ближайшим к обоим сразу.
    """
    return bool(landmarks) and all(lm.nearest_only for lm in landmarks)


def _apply_superlative(
    eligible: list[ComplexCandidate],
    landmarks: list[LandmarkRequirement],
    names: str,
    warnings: list[str],
) -> list[str]:
    """id ближайших ЖК для суперлативного запроса + warning об этом.

    Общий для обеих веток (ориентир один и ориентир вместе с POI/центром), чтобы
    текст предупреждения и лимит жили в одном месте. ``eligible`` — кандидаты,
    уже прошедшие остальные требования: суперлатив заменяет РАДИУС, а не весь
    фильтр, поэтому сужать список обязан вызывающий, а не эта функция.
    """
    nearest_ids, nearest_m = landmark_nearest(eligible, landmarks)
    if nearest_ids:
        warnings.append(
            f"ближайшие к {names}: у pik.ru такого фильтра нет — показаны "
            f"{len(nearest_ids)} ближайших ЖК, от {nearest_m / 1000:.2f} км"
        )
    return nearest_ids


#: Человекочитаемые названия POI-категорий для пояснений пользователю.
_POI_CATEGORY_LABELS = {
    "school": "школа",
    "kindergarten": "детский сад",
    "shop": "магазин",
    "parking": "парковка",
    "park_forest": "парк/лес",
    "medical": "медицина",
}


def _poi_object_label(candidate: ComplexCandidate, cat: str) -> str:
    """Как назвать ближайший объект категории у конкретного ЖК.

    Пустое имя значит РАЗНОЕ в двух схемах кэша, и подменять одно другим нельзя
    (инвариант 1): ``closest_unnamed=True`` — установленный факт схемы v2 (на
    карте это просто двор), тогда как в v1 имена не сохранялись вовсе, и там
    пустое имя честно значит «в кэше не записано». Именно поэтому читается флаг
    ``poi_unnamed``, а не просто ``poi_names[cat] is None``: он единственный
    отличает «безымянный» от «неизвестно».
    """
    name = candidate.poi_names.get(cat)
    if name:
        return f"«{name}»"
    if candidate.poi_unnamed.get(cat):
        return "объект без названия в OSM"
    return "объект (имя в кэше не записано)"


def _warn_poi_evidence(
    criteria: Criteria,
    candidates: list[ComplexCandidate],
    matched_ids: list[str],
    warnings: list[str],
) -> None:
    """Назвать объект, из-за которого ЖК прошли POI-требование.

    Живой прогон: пользователь получил ссылку, где кэш обещал сад в 186 м, и
    садов на карте не нашёл (это была стройплощадка). Одного числа метров
    недостаточно — пользователь должен видеть ИМЯ объекта, чтобы проверить его
    сам. Одна строка на категорию — когда требование участвовало в отборе и
    хотя бы один ЖК его прошёл (условие ровно такое: непустой ``matched_ids``;
    сужения выдачи НЕ требуется — имя и дистанция нужны для самопроверки и
    тогда, когда прошли все кандидаты). Строящиеся объекты упоминаем числом, а
    если стройка ближе действующего объекта — ещё и дистанцией: это ровно то
    число, которое объясняет «сад в 186 м» (инвариант 1 — не молчим).

    Для записи кэша v1 формулировка мягче: там действующие и строящиеся объекты
    не разделены, и утверждать «ближайший ДЕЙСТВУЮЩИЙ» мы права не имеем.
    """
    if not criteria.poi_requirements or not matched_ids:
        return

    matched = [c for c in candidates if c.id in set(matched_ids)]
    for req in criteria.poi_requirements:
        cat = req.category.value
        label = _POI_CATEGORY_LABELS.get(cat, cat)
        scored = sorted(
            ((c.poi_distances[cat], c) for c in matched if c.poi_distances.get(cat) is not None),
            key=lambda pair: pair[0],
        )
        if not scored:
            continue
        dist, nearest = scored[0]
        is_v2 = nearest.poi_schema_version.get(cat, 1) >= POI_CACHE_SCHEMA_VERSION
        who = _poi_object_label(nearest, cat)
        kind = "ближайший действующий" if is_v2 else "ближайший"
        note = f"{label}: {kind} — {who}, {dist:.0f} м (ЖК «{nearest.name}»)"
        if not is_v2:
            note += "; запись кэша v1 — стройки в ней не отделены от работающих объектов"
        under_construction = sum(c.poi_under_construction.get(cat, 0) for c in matched)
        if under_construction:
            note += f"; строящихся объектов не учтено: {under_construction}"
            closer = nearest.poi_under_construction_m.get(cat)
            if closer is not None and closer < dist:
                note += f" (ближайшая стройка ближе — {closer:.0f} м)"
        warnings.append(note)


def _warn_mixed_superlative(superlative: list[LandmarkRequirement], warnings: list[str]) -> None:
    """Смешанный запрос: суперлатив не применяем, но и не проглатываем молча."""
    sup_names = ", ".join(f"«{lm.name}»" for lm in superlative)
    warnings.append(
        f"«ближайшие к {sup_names}» вместе с другими ориентирами не "
        f"поддерживается — все ориентиры учтены как «рядом»"
    )


async def enrich(
    text: str,
    criteria: Criteria,
    warnings: list[str],
    pool: asyncpg.Pool | None = None,
    option_candidates: list[str] | None = None,
) -> EnrichmentResult:
    # Ветка резолвинга опций независима от шорт-листа ЖК: фразы-синонимы
    # фильтров надо добить, даже если гео-кандидатов нет. Мутирует criteria и
    # warnings на месте.
    await resolve_options(criteria, option_candidates or [], warnings, pool)

    # Экстрактор свободного текста (ведро C) — ДО гейта 1 и независимо от него:
    # достаёт недостающие СКАЛЯРНЫЕ фильтры из непонятого парсером текста. Это
    # ослабление гейтов: запрос без структурных сигналов, но с остатком
    # «не удалось распознать», теперь доходит до ИИ. Мутирует criteria/warnings
    # на месте; результат учитываем в ai_used/логах ниже (free_text).
    free_text = await resolve_free_text_criteria(criteria, text, warnings, pool)

    # Наблюдаемость (Milestone AI-11): собираем поля для ai_call_log по мере
    # прохождения пайплайна и пишем ОДНУ строку на каждый вызов enrich() —
    # вне зависимости от исхода — через _log() ниже. had_poi_or_center = сработал
    # бы старый гейт 1 (см. закомментированный if ниже). ai_called/
    # criteria_changed_by_ai инициализируются исходом экстрактора свободного
    # текста (он тоже вызов ИИ) — при POI-пути ниже они уточняются повторно.
    log_fields = {
        "had_poi_or_center": bool(criteria.poi_requirements or criteria.center_requested),
        "fully_resolved_deterministically": False,
        "cache_hit": False,
        "ai_called": free_text.called,
        "criteria_changed_by_ai": free_text.changed,
    }

    async def _log(result: EnrichmentResult) -> EnrichmentResult:
        # Экстрактор свободного текста реально повлиял на criteria (мутировал его
        # ДО гейтов) — отражаем это в ai_used честно, даже если путь ниже вернул
        # noop()/from_deterministic (у которых ai_used=False по конструкции).
        if free_text.changed:
            log_fields["criteria_changed_by_ai"] = True
            if not result.ai_used:
                result.ai_used = True
                result.explanation = (
                    f"{result.explanation} {free_text.explanation}".strip()
                    if result.explanation
                    else free_text.explanation or None
                )
        try:
            await log_ai_call(pool, **log_fields)
        except Exception as e:
            logger.warning(f"Failed to write ai_call_log: {e}")
        return result

    # Гейт 1 (Milestone AI-14 — включён; гейт 2 ниже остаётся выключен, см.
    # docs/ai-enrichment-architecture.md, «Критерий возврата гейтов»). Не звать
    # ничего из ИИ-пути (ни шорт-лист, ни семантический кэш, ни сам call_model
    # ниже), если в запросе нет вообще ничего, что можно обогатить: ни
    # poi_requirements, ни center_requested, ни непустых option_candidates.
    # resolve_options() выше уже отработал независимо от этого гейта (мутирует
    # criteria/warnings на месте до сюда) — его результат не теряется вне
    # зависимости от исхода этой проверки. option_candidates из условия
    # ИСКЛЮЧЕНЫ (Milestone AI-21): раньше они «для читаемости» пропускали
    # запрос дальше, и option-only запрос («…Троицкой ветки» с огрызком
    # «ветки») доходил до шорт-листа, где resolve_known_facts при ПУСТЫХ
    # семантических требованиях объявлял совпавшими ВСЕХ кандидатов (vacuous
    # truth), а включённый гейт 2 выливал полкаталога в blocks. После
    # resolve_options() опциям в ИИ-пути делать больше нечего.
    #
    # landmark_requirements — ОБЯЗАТЕЛЬНОЕ исключение, а не часть буквального
    # условия из ТЗ на этот гейт: сужение по ориентиру (Milestone AI-13,
    # «однушка рядом с МГУ») — отдельная, всегда включённая ветка чистой
    # математики НИЖЕ по коду, которая сама не зовёт ИИ и сама решает, доходить
    # ли до gate 2/call_model. Если бы гейт 1 не пропускал landmark-only запросы
    # сюда, они бы молча схлопывались в noop() ДО этой ветки — регрессия того
    # самого бага AI-12, который чинили отдельно. Проверено падением 5 тестов
    # (test_enrich_resolves_landmark_*, test_enrich_landmark_*) при попытке
    # ограничиться буквальным условием без этого пункта.
    #
    # station_class_requirements (Milestone AI-15, «рядом с МЦД не важно какой
    # станции») — то же обязательное исключение, что и landmark_requirements
    # выше: своя отдельная всегда включённая ветка чистой математики ниже по
    # коду (не зовёт ИИ сама), гейт 1 не должен схлопывать такие запросы в
    # noop() до неё.
    if (
        not criteria.poi_requirements
        and not criteria.center_requested
        and not criteria.landmark_requirements
        and not criteria.station_class_requirements
    ):
        return await _log(EnrichmentResult.noop())

    candidates = build_candidate_shortlist(criteria, warnings)
    if not candidates:
        # Ориентир с явной дистанцией мог законно обнулить шорт-лист
        # (`_rank_by_landmark` применяет max_distance_m как жёсткую отсечку).
        # Это НЕ отказ ИИ: ai_failed=True по контракту означает «попытка была и
        # упала», а здесь модель не звали вовсе. Плюс «Список кандидатов пуст»
        # пользователю ничего не объясняет — называем ориентир и дистанцию.
        if criteria.landmark_requirements:
            landmark_names = ", ".join(f"«{lm.name}»" for lm in criteria.landmark_requirements)
            declared = [
                lm.max_distance_m
                for lm in criteria.landmark_requirements
                if lm.max_distance_m is not None
            ]
            limit_note = f" в пределах {min(declared)} м" if declared else ""
            warnings.append(f"рядом с {landmark_names}{limit_note} подходящих ЖК не найдено")
            # НЕ noop(): ориентир РЕАЛЬНО участвовал (жёсткая отсечка дистанции
            # обнулила шорт-лист ещё до подсчёта) и дал легитимный ноль — это
            # отличается от «нечего было считать» (см. EnrichmentResult.complexes_matched).
            # Иначе merge_enrichment не трогал criteria.complexes, и AND с
            # гео-фолбэком (МКАД и т.п.) молча откатывался на весь фолбэк-список,
            # как будто ориентира не было вовсе (Дефект №1).
            return await _log(
                EnrichmentResult(
                    ai_used=False,
                    cache_hit=False,
                    success=True,
                    matched_complex_ids=[],
                    complexes_matched=True,
                )
            )
        warnings.append("Список кандидатов пуст")
        return await _log(EnrichmentResult.failed())

    known = resolve_known_facts(candidates, criteria)

    # fully_resolved() вычисляется ВСЕГДА (даже пока гейт 2 закомментирован) —
    # это и есть измерение «что было бы, если включить гейт 2».
    log_fields["fully_resolved_deterministically"] = fully_resolved(known, criteria, candidates)

    # «В центре» не выполнимо в принципе — говорим об этом прямо. Замер по
    # справочникам: у ЖК ПИК встречаются округа ВАО/ЗАО/САО/СВАО/СЗАО/ЮАО/ЮВАО/
    # ЮЗАО/Новомосковский/Щербинка, ЦАО отсутствует полностью; из районов ЦАО в
    # districts.json есть только Таганский, и ЖК в нём тоже нет. Это факт
    # портфеля застройщика (тот же класс, что «внутри Садового кольца новостроек
    # ПИК нет» из AI-18), а не пробел справочника, поэтому лечится честным
    # предупреждением, а не проставлением is_center. Без него требование
    # «в центре» исчезало совсем молча: matched пуст, а причина неизвестна.
    if criteria.center_requested and not any(c.is_center for c in candidates):
        warnings.append(
            "«в центре»: у pik.ru нет новостроек в центральных районах Москвы — "
            "требование не применено"
        )

    # Сужение по ориентиру («рядом с МГУ» и т.п.) — чистая математика
    # (haversine на lat/lon, см. app/geo/candidates.resolve_known_facts), а не
    # работа ИИ. Это НЕ гейт 1/2 выше (те про POI/центр и остаются выключены по
    # Milestone AI-11) — отдельный, всегда включённый шаг именно для
    # landmark_requirements: должен отрабатывать детерминированно, даже когда
    # ИИ выключен или недоступен (иначе распознанный ориентир с координатами
    # молча пропадает — см. AI-12 аудит бага «однушка рядом с МГУ подешевле»).
    # Если запрос требует ЕЩЁ и POI/центр — оставляем как есть, там своя
    # (пока ИИ-зависимая) логика ниже.
    if (
        criteria.landmark_requirements
        and not criteria.poi_requirements
        and not criteria.center_requested
    ):
        names = ", ".join(f"«{lm.name}»" for lm in criteria.landmark_requirements)
        if not any(c.lat is not None and c.lon is not None for c in candidates):
            # Ни у одного кандидата нет координат — сужение физически
            # невозможно (см. app/reference/complexes.json). Инвариант
            # «ничего не отбрасывается молча»: честно предупреждаем, а не
            # тихо возвращаем исходные (неотфильтрованные) criteria.
            warnings.append(
                f"не удалось сузить список ЖК рядом с {names}: в справочнике ЖК нет координат"
            )
            return await _log(EnrichmentResult.noop())

        matched_complex_ids = known["matched_complex_ids"]

        # Суперлатив («самую ближайшую к Политеху») — не радиус, а минимум
        # дистанции (Milestone AI-22). resolve_known_facts выше отвечает на
        # вопрос «в радиусе ли», а спрошено было другое, поэтому его ответ здесь
        # заменяется целиком: и когда радиус пуст (ближайший дальше 5 км), и
        # когда в него попало полгорода (все «рядом», но спрошены ближайшие).
        # ВСЕ ориентиры суперлативные — только тогда меняем семантику. Смешанный
        # запрос («рядом с МГУ и ближайшую к Политеху») раньше проходил по `any`
        # и отбрасывал радиусное требование целиком, а warning утверждал
        # «ближайшие к обоим», хотя ни один ЖК не ближайший к обоим сразу.
        superlative = [lm for lm in criteria.landmark_requirements if lm.nearest_only]
        if _all_superlative(criteria.landmark_requirements):
            # Здесь сужать нечем — POI/центра в этой ветке по условию нет, поэтому
            # «прошедшие остальные требования» это и есть все кандидаты. Пусто
            # бывает, только если координат нет вовсе или явная дистанция отсекла
            # всё — тогда честный warning ниже по общему пути.
            matched_complex_ids = _apply_superlative(
                candidates, criteria.landmark_requirements, names, warnings
            )
        else:
            if superlative:
                _warn_mixed_superlative(superlative, warnings)

            if not matched_complex_ids:
                # Обычное «рядом с X», радиус дал пусто — осмысленная деградация
                # вместо тишины, ровно как у станций класса ниже (Milestone AI-18).
                # Фолбэк сам вернёт ([], None), если задана явная дистанция: тогда
                # это жёсткая отсечка, и пустой ответ правильный.
                fallback_candidates, fallback_warning = landmark_nearest_fallback(
                    candidates, criteria.landmark_requirements, names
                )
                if fallback_warning:
                    warnings.append(fallback_warning)
                    matched_complex_ids = [c.id for c in fallback_candidates]

        if not matched_complex_ids:
            warnings.append(f"рядом с {names} подходящих ЖК не найдено")

        result = EnrichmentResult(
            ai_used=False,
            cache_hit=False,
            success=True,
            matched_complex_ids=matched_complex_ids,
            complexes_matched=True,
        )
        return await _log(result)

    # Ориентир В КОМБИНАЦИИ с POI/центром: ветка выше пропущена по условию, но
    # радиус ориентира уже применён внутри resolve_known_facts. Если он не
    # оставил ни одного ЖК — сказать об этом надо здесь и сейчас: дальше по коду
    # пустой matched неотличим от «ИИ ничего не нашёл», и требование «рядом с X»
    # исчезало совсем молча. Один и тот же запрос вёл себя противоположно в
    # зависимости от того, добавил ли пользователь «со школой рядом»
    # (инвариант 1: ничего не отбрасывается молча).
    if criteria.landmark_requirements:
        landmark_names = ", ".join(f"«{lm.name}»" for lm in criteria.landmark_requirements)
        superlative = [lm for lm in criteria.landmark_requirements if lm.nearest_only]

        if _all_superlative(criteria.landmark_requirements):
            # Суперлатив теряется здесь исторически: признак nearest_only читала
            # ТОЛЬКО ветка выше, закрытая условием `not poi_requirements`. Живой
            # прогон «трёшка ближайшая к Политеху … рядом детские сады»: признак
            # выставлен парсером, но запрос молча деградировал в радиус 5 км. На
            # данных справочника трактовки расходятся у 45 ориентиров из 50, а у
            # МГИМО/МФТИ/ХХС радиус даёт 0 ЖК там, где суперлатив даёт 3.
            #
            # resolve_known_facts ответил на вопрос «в радиусе ли», а спрошен был
            # минимум дистанции — поэтому пересчитываем. Кандидатов сужаем теми же
            # requirements, но БЕЗ ориентира: POI/центр/класс станций остаются
            # обязательными (суперлатив заменяет радиус, а не весь фильтр), а
            # радиусная отсечка ориентира уходит.
            without_landmarks = criteria.model_copy(update={"landmark_requirements": []})
            eligible_ids = set(
                resolve_known_facts(candidates, without_landmarks)["matched_complex_ids"]
            )
            eligible = [c for c in candidates if c.id in eligible_ids]
            known["matched_complex_ids"] = _apply_superlative(
                eligible, criteria.landmark_requirements, landmark_names, warnings
            )
        elif superlative:
            _warn_mixed_superlative(superlative, warnings)

        if not known["matched_complex_ids"]:
            warnings.append(f"рядом с {landmark_names} подходящих ЖК не найдено")

    # Прозрачность POI: назвать объект, по которому ЖК прошли требование. Ставим
    # здесь — ниже все ветки, где poi_requirements по условию пусты (landmark-
    # only, station-class-only), поэтому лишних строк не будет, а обе ветви ниже
    # (гейт 2 и ИИ-путь) сообщение получат.
    _warn_poi_evidence(criteria, candidates, known["matched_complex_ids"], warnings)

    # Сужение по классу станций («рядом с МЦД не важно какой станции», «у
    # любого метро» — Milestone AI-15) — то же обобщение ориентира на КЛАСС
    # точек: чистая математика (haversine до БЛИЖАЙШЕЙ станции подходящего
    # класса, см. app.geo.candidates.resolve_known_facts), а не работа ИИ.
    # Всегда включённая ветка по тем же причинам, что и landmark-ветка выше:
    # должна отрабатывать детерминированно независимо от доступности ИИ.
    if (
        criteria.station_class_requirements
        and not criteria.poi_requirements
        and not criteria.center_requested
        and not criteria.landmark_requirements
    ):
        names = ", ".join(f"«{r.line_prefix}»" for r in criteria.station_class_requirements)

        if not station_class_points(criteria):
            # Ни у одной станции подходящего класса нет координат — сужение
            # физически невозможно (справочник metro.json ещё не обогащён
            # полями lat/lon/line). Инвариант «ничего не отбрасывается молча»:
            # честно предупреждаем, а не тихо возвращаем исходные criteria.
            warnings.append(
                f"не удалось сузить список ЖК рядом со станциями класса {names}: "
                "в справочнике метро нет координат нужных станций"
            )
            return await _log(EnrichmentResult.noop())

        if not any(c.lat is not None and c.lon is not None for c in candidates):
            warnings.append(
                f"не удалось сузить список ЖК рядом со станциями класса {names}: "
                "в справочнике ЖК нет координат"
            )
            return await _log(EnrichmentResult.noop())

        matched_complex_ids = known["matched_complex_ids"]
        if not matched_complex_ids:
            # Осмысленная деградация (Milestone AI-18): пустая выдача в радиусе
            # по умолчанию — не повод молчать, если сайт может показать
            # ближайшие варианты. Фолбэк сам возвращает ([], None), если
            # пользователь задал явную дистанцию (max_distance_m) — тогда это
            # жёсткая отсечка, и прежнее поведение (пустой warning) правильное.
            fallback_candidates, fallback_warning = station_class_nearest_fallback(
                candidates, criteria.station_class_requirements, names
            )
            if fallback_warning:
                warnings.append(fallback_warning)
                matched_complex_ids = [c.id for c in fallback_candidates]
            else:
                warnings.append(f"рядом со станциями класса {names} подходящих ЖК не найдено")

        result = EnrichmentResult(
            ai_used=False,
            cache_hit=False,
            success=True,
            matched_complex_ids=matched_complex_ids,
            complexes_matched=True,
        )
        return await _log(result)

    # Гейт 2 ВКЛЮЧЁН (Milestone AI-20). Формальный критерий раздела 8.4
    # (fully_resolved ≥ 90% над N ≥ 500) был недостижим в принципе:
    # poi_cache.json стоял пустым (сбор срывался из-за недоступности
    # overpass-api.de — починено зеркалами в app/geo/poi.py), и
    # fully_resolved физически не мог стать True — самозамыкающаяся петля,
    # гейт ждал статистику, которая не могла набраться. После полного сбора
    # кэша (69 ЖК × 5 категорий) и живого 429-инцидента гейт включён по
    # эксплуатационному сигналу — тот же осознанный прецедент, что и гейт 1
    # (Milestone AI-14). fully_resolved остаётся консервативным: любой
    # неизвестный факт (нет категории в кэше, only_new, неизвестный центр)
    # по-прежнему уводит в ИИ, а не додумывается.
    # Страховка от vacuous truth (Milestone AI-21): при ПУСТЫХ семантических
    # требованиях resolve_known_facts объявляет совпавшими всех кандидатов, и
    # «полностью решено детерминированно» означало бы «вылить весь шорт-лист в
    # blocks». Гейт 1 такие запросы сюда уже не пускает, но защита обязана
    # жить и здесь — на случай будущих правок порядка ветвей выше.
    if log_fields["fully_resolved_deterministically"] and (
        criteria.poi_requirements or criteria.center_requested
    ):
        return await _log(EnrichmentResult.from_deterministic(known))

    settings = get_settings()
    # .lower() — как в client.call_typed: AI_PROVIDER=Claude не должен
    # проскакивать гейт и падать уже внутри клиента. Учётными данными
    # считается и API-ключ, и OAuth-сессия Claude Code
    # (см. app.ai.client.claude_credentials_available).
    is_claude_missing = settings.AI_PROVIDER.lower() == "claude" and not (
        claude_credentials_available(settings)
    )

    if not settings.AI_ENRICHMENT_ENABLED or is_claude_missing:
        if criteria.poi_requirements or criteria.center_requested:
            warnings.append("ИИ-обогащение выключено — часть запроса не обработана")
        return await _log(EnrichmentResult.disabled())

    signature = build_query_signature(text, criteria)
    embedding = embed(signature)

    try:
        if pool is not None:
            cached = await lookup_semantic(pool, signature, embedding)
        else:
            cached = None
    except Exception as e:
        logger.warning(f"Failed to lookup semantic cache: {e}")
        cached = None

    if cached is not None:
        result = EnrichmentResult.from_cache(cached)
        log_fields["cache_hit"] = True
        log_fields["criteria_changed_by_ai"] = _differs_from_deterministic(result, known)
        return await _log(result)

    log_fields["ai_called"] = True
    try:
        context = build_context(text, criteria, candidates, known)
        answer = await call_model(SYSTEM_PROMPT, context)
    except CircuitOpenError as e:
        # Circuit breaker открыт (часть B): серия транзиентных отказов подряд —
        # ИИ-слой в эту попытку вовсе не звался (не разовый сбой, а осознанный
        # cooldown-предохранитель). Текст намеренно отличается от «ошибка
        # сервиса» ниже — пользователю нужно различать «сервис временно
        # перегружен, попробуй позже» и «однократная ошибка».
        logger.warning(f"AI enrichment skipped (circuit breaker open): {e}")
        unmet = _describe_unmet_ai_requirements(criteria)
        warnings.append(
            f"требование {unmet} не удалось применить — ИИ-слой временно "
            "деградирован (серия сбоев подряд), выдача не сужена"
        )
        return await _log(EnrichmentResult.failed())
    except Exception as e:
        from claude_agent_sdk import ClaudeSDKError
        from pydantic import ValidationError

        # Транзиентность и ретраи уже инкапсулированы в client (backoff +
        # circuit breaker); сюда долетает лишь исчерпавший ретраи/фатальный
        # сбой. Ловим ошибки транспорта Claude (ClaudeSDKError — сбои CLI;
        # RuntimeError — наши ClaudeSDKCallError/ClaudeStreamError и agy;
        # OSError — запуск agy), нет учётных данных/выключено (ValueError) и
        # брак ответа модели (ValidationError). Прочее (баг в коде) — пробросить.
        if isinstance(e, (ClaudeSDKError, RuntimeError, OSError, ValueError, ValidationError)):
            logger.error(f"AI enrichment failed: {e}", exc_info=True)
            # Предметная деградация (часть C): называем, ЧТО именно не
            # применилось (POI-требования/центр), а не обезличенное «ошибка
            # сервиса» — по образцу landmark/station-class веток выше.
            unmet = _describe_unmet_ai_requirements(criteria)
            warnings.append(
                f"требование {unmet} не удалось применить — ИИ-слой недоступен "
                "(ошибка сервиса), выдача не сужена"
            )
            return await _log(EnrichmentResult.failed())
        raise e

    answer = sanitize_against_shortlist(answer, candidates)

    try:
        await persist(answer, signature, text, pool)
    except Exception as e:
        logger.warning(f"Failed to persist AI results to DB: {e}")

    result = EnrichmentResult.from_ai(answer)
    log_fields["criteria_changed_by_ai"] = _differs_from_deterministic(result, known)
    return await _log(result)
