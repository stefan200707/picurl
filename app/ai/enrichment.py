import logging

import asyncpg
from pydantic import BaseModel

from app.ai.client import (
    CircuitOpenError,
    call_model,
    call_option_resolver,
    resolve_claude_credentials,
)
from app.ai.embeddings import embed
from app.ai.memory import (
    log_ai_call,
    lookup_semantic,
    store_semantic,
    store_structured_fact,
)
from app.ai.prompts import OPTION_SYSTEM_PROMPT, SYSTEM_PROMPT, build_context, build_option_context
from app.ai.schema import AIEnrichmentAnswer, ComplexCandidate, OptionResolutionAnswer
from app.config import get_settings
from app.geo.candidates import (
    build_candidate_shortlist,
    build_query_signature,
    fully_resolved,
    resolve_known_facts,
    station_class_nearest_fallback,
    station_class_points,
)
from app.parsing.schema import Criteria
from app.reference.loader import load_option_groups, load_options, normalize

#: fact_type для логирования сопоставлений «фраза → slug фильтра» в
#: ai_structured_facts (переиспользуем карту памяти промпта 17). Промоушен этих
#: наблюдений в aliases справочников делает app/ai/promotion.py.
OPTION_ALIAS_FACT_TYPE = "option_alias"

logger = logging.getLogger(__name__)


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
            center_district_ids=answer.center_district_ids,
            poi_findings=answer.poi_findings,
            explanation=answer.explanation,
        )


def merge_enrichment(criteria: Criteria, enrichment: EnrichmentResult) -> Criteria:
    # Импорт поднят один раз в начало функции (AUDIT_REPORT 2.6): раньше
    # MatchedEntity импортировался дважды в двух разных if-ветках.
    from app.parsing.schema import MatchedEntity

    if enrichment.matched_complex_ids:
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
    # (см. app.ai.client.resolve_claude_credentials).
    is_claude_missing = (
        settings.AI_PROVIDER.lower() == "claude" and resolve_claude_credentials(settings) is None
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

    # Наблюдаемость (Milestone AI-11): собираем поля для ai_call_log по мере
    # прохождения пайплайна и пишем ОДНУ строку на каждый вызов enrich() —
    # вне зависимости от исхода — через _log() ниже. had_poi_or_center = сработал
    # бы старый гейт 1 (см. закомментированный if ниже).
    log_fields = {
        "had_poi_or_center": bool(criteria.poi_requirements or criteria.center_requested),
        "fully_resolved_deterministically": False,
        "cache_hit": False,
        "ai_called": False,
        "criteria_changed_by_ai": False,
    }

    async def _log(result: EnrichmentResult) -> EnrichmentResult:
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

    candidates = build_candidate_shortlist(criteria)
    if not candidates:
        warnings.append("Список кандидатов пуст")
        return await _log(EnrichmentResult.failed())

    known = resolve_known_facts(candidates, criteria)

    # fully_resolved() вычисляется ВСЕГДА (даже пока гейт 2 закомментирован) —
    # это и есть измерение «что было бы, если включить гейт 2».
    log_fields["fully_resolved_deterministically"] = fully_resolved(known, criteria, candidates)

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

        if not known["matched_complex_ids"]:
            warnings.append(f"рядом с {names} подходящих ЖК не найдено")

        result = EnrichmentResult(
            ai_used=False,
            cache_hit=False,
            success=True,
            matched_complex_ids=known["matched_complex_ids"],
        )
        return await _log(result)

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
    # (см. app.ai.client.resolve_claude_credentials).
    is_claude_missing = (
        settings.AI_PROVIDER.lower() == "claude" and resolve_claude_credentials(settings) is None
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
        from anthropic import APIStatusError, APITimeoutError
        from pydantic import ValidationError

        if isinstance(e, (APIStatusError, APITimeoutError, ValueError, ValidationError)):
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
