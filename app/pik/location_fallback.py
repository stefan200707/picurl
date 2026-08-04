"""Geo-фолбэк на ``blocks`` для локационных сущностей без достоверного id.

Контекст (аудит validate(), 2026-07-23, живые замеры curl против
``api.pik.ru/v2/filter``): единственный локационный query-параметр, который
бэкенд валидации реально проверяет — ``blocks`` (id ЖК); ``metroStations``/
``districtLocations``/``districtCounties`` он молча игнорирует независимо от
корректности значения (``result_count`` для baseline/реального GUID/заведомо
фейкового GUID/несуществующего id округа совпал побайтово). Отдельно от этого,
часть id в ``metro.json`` доказанно или предположительно синтетические
(``app.pik.id_trust``).

Раз единственный проверяемый механизм — ``blocks``, а у ЖК справочника уже
есть точная привязка к метро/району/округу (из живых данных pik.ru,
``app.reference.refresh``) и у станций метро — координаты
(``app.reference.refresh_metro_geo``), локационную сущность без достоверного
id можно заменить ПРОВЕРЯЕМЫМ сужением по ``blocks`` вместо того, чтобы либо
тихо ничего не делать (поведение до этой правки — см. ``_missing_id_warning``
в ``app.parsing.parser``), либо подставлять фейковый id в URL.

Порядок фолбэка для каждой недостоверной сущности:
    1. Точный тег-матч (:func:`app.geo.candidates.block_ids_by_tag`) — ЖК, у
       которых поле привязки буквально равно имени сущности. Не приближение —
       прямое сопоставление по факту из тех же живых данных, из которых взят
       id самих ЖК.
    2. Если тегом ничего не нашлось (сущность существует, но пока ни один ЖК
       ПИК не привязан к ней напрямую) и у сущности есть координаты (сейчас —
       только метро, после ``refresh_metro_geo``) — сужение по расстоянию
       (:func:`app.geo.candidates.nearby_block_ids`): в радиусе, либо (если
       пусто) N ближайших — тот же приём, что и Milestone AI-18.
    3. Если ни то, ни другое не дало результата — сущность не участвует в
       ссылке вообще (как и до этой правки); честное предупреждение об этом
       уже формирует ``app.parsing.parser`` на этапе разбора
       (``_missing_id_warning``), здесь же добавляется собственная причина.

Какие сущности считаются «без достоверного id» (ничего не трогаем из того,
что и так уже работает штатно — путь по ``slug`` или query по ``id``):
    - **метро** — нет ``slug`` (иначе одиночный выбор по-прежнему работает
      через путь URL) И id не прошёл
      :func:`app.pik.id_trust.is_verified_metro_id`;
    - **округа** — нет ``slug`` И нет ``id``. Отдельный реестр недостоверности
      (как у метро) не нужен: после починки ``app.reference.refresh`` id
      округа, если он есть, происходит из живого ``api.pik.ru/v2/block``
      (``locations.child.id``) и по построению достоверен;
    - **районы** — нет ``id``. У районов в текущей схеме вообще нет пути по
      ``slug`` (см. ``app/pik/url_builder.py``), а id (``districtLocations``)
      живой API не отдаёт вовсе — докуривается вручную и не подтверждаем
      никаким источником; отдельного реестра тоже не нужно: раз проверить
      нечем, единственная альтернатива тишине без замены — тег-фолбэк ниже.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.geo.candidates import (
    STATION_CLASS_DEFAULT_RADIUS_M,
    STATION_CLASS_FALLBACK_LIMIT,
    block_ids_by_tag,
    complexes_in_mkad,
    nearby_block_ids,
)
from app.geo.distance import STRAIGHT_LINE_NOTE
from app.parsing.schema import Criteria, MatchedEntity
from app.pik.id_trust import is_verified_metro_id
from app.reference.loader import find_by_name, load_metro


@dataclass
class LocationFallback:
    """Результат geo-фолбэка: собранные id ЖК + человекочитаемые причины."""

    block_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


#: Единый текст предупреждения о пустом пересечении локационных требований.
#: Раньше жил только в ``url_builder.build_url`` (текстом внутри функции), и
#: ``validator.validate`` его вообще не воспроизводил (Дефект №2) — теперь оба
#: места используют одну константу, чтобы не разойтись формулировкой снова.
LOCATION_INTERSECTION_EMPTY_WARNING = (
    "гео-условия запроса не пересекаются: ЖК, подходящие сразу под все "
    "локационные требования, не найдены — показаны подходящие под часть"
)

#: Оговорка к заметке фолбэка, чьи ЖК не пережили пересечение с другим
#: гео-условием. Заметка сообщает «показаны N ближайших» — утверждение о
#: ВЫДАЧЕ, а не о расчёте, и после отсева оно становится ложным. Удалять
#: заметку нельзя (инвариант 1: факт расчёта — тоже факт), поэтому она
#: остаётся, но перестаёт утверждать то, чего в ссылке нет.
FALLBACK_NOTE_DROPPED_SUFFIX = " — но в ссылку не попали: отсеяны другим гео-условием"

#: «Посчитали и получили ноль» — а выразить этот ноль в ссылке нечем (Д1).
#: Пустой ``blocks=`` на pik.ru СНИМАЕТ фильтр: исход не «ни одного ЖК», а «весь
#: город», то есть ШИРЕ любого списка. Поэтому вместо невыразимого нуля отдаём
#: сужение по метро/району (оно строго уже, чем весь город) и честно говорим,
#: какое требование при этом не применено. Прежнее поведение (пустой список)
#: было формально верным ответом на вопрос «сколько ЖК подошло» и заведомо
#: неверным ответом на вопрос «что увидит пользователь».
LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING = (
    "гео-требование, по которому не осталось ни одного ЖК, применить не удалось: "
    "«ноль ЖК» в ссылке невыразим — пустой blocks= на pik.ru снимает фильтр и "
    "показывает весь город. Показаны ЖК, отобранные только по метро/району"
)

#: Итоговый список ЖК пуст по любой причине — ключ ``blocks`` в query не пишем
#: вовсе. Молчать нельзя (инвариант 1): отсутствие параметра пользователь
#: прочтёт как «локацию учли», а её не учли ничем.
LOCATION_NARROWING_NOT_APPLIED_WARNING = (
    "локационное сужение не применено: список ЖК пуст, а пустой blocks= на "
    "pik.ru снимает фильтр — параметр в ссылку не добавлен"
)


def combine_with_fallback(
    existing_block_ids: list[str],
    fallback: LocationFallback,
    criteria: Criteria,
    warnings: list[str] | None = None,
) -> list[str]:
    """Скомбинировать уже выбранные ЖК с geo-фолбэком (МКАД/метро-дистанция/тег).

    ПЕРЕСЕЧЕНИЕ, а не объединение — общая логика для
    ``app.pik.url_builder.build_url`` и ``app.pik.validator.validate`` (Дефект
    №2: раньше validate() ОБЪЕДИНЯЛ фолбэк с уже выбранными ЖК, из-за чего
    result_count расходился с build_url в разы). Оба списка сужают одну и ту же
    ось «какие ЖК», поэтому OR стирал бы более узкое требование — тот же класс
    дефекта, что потеря суперлатива при POI (AI-23).

    Вызывать имеет смысл, только когда ``fallback.block_ids`` непусто (иначе
    сочетать нечего) — эту гарантию соблюдают оба вызывающих места.

    ``criteria.complexes_matched_empty`` (Дефект №1) отличает «ЖК не выбирались
    вовсе» от «сужение по ориентиру/POI/центру РЕАЛЬНО посчиталось и дало ноль».
    Различие сохраняется, но исход у него теперь другой (Д1, 2026-08-04): раньше
    считаный ноль оставлял ``blocks`` ПУСТЫМ, и это выдавалось за «пересечение
    пусто». На pik.ru пустой ``blocks=`` не сужает выдачу, а СНИМАЕТ фильтр —
    считаный ноль превращался в «весь город», то есть в исход ШИРЕ любого
    списка. Выразить ноль в этом параметре нечем в принципе, поэтому:

    * есть непустой специфичный список — правило AND в силе, он и побеждает
      (:data:`LOCATION_INTERSECTION_EMPTY_WARNING`);
    * специфичного списка нет (считаный ноль) — отдаём фолбэк, который строго
      уже, чем весь город, и честно называем неприменённое требование
      (:data:`LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING`).
    """
    if not existing_block_ids:
        # Сюда попадают оба случая без специфичного списка: «ЖК не выбирались
        # вовсе» (как раньше, молча) и «посчитали ноль» (с предупреждением —
        # требование пользователя в ссылку не доехало, инвариант 1).
        if warnings is not None and criteria.complexes_matched_empty:
            warnings.append(LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING)
        return list(dict.fromkeys(fallback.block_ids))

    narrowed = [b for b in existing_block_ids if b in set(fallback.block_ids)]
    if not narrowed:
        # Требования несовместимы (или обе стороны легитимно пусты).
        # Расширяться до объединения нельзя (это и был баг), поэтому
        # оставляем более специфичный список — тот, что пришёл из семантики
        # запроса (ориентир/POI/названный ЖК), — и честно предупреждаем.
        if warnings is not None:
            warnings.append(LOCATION_INTERSECTION_EMPTY_WARNING)
        narrowed = existing_block_ids
    return list(dict.fromkeys(narrowed))


def _metro_needs_fallback(entity: MatchedEntity) -> bool:
    return not entity.slug and not is_verified_metro_id(entity.id)


def _county_needs_fallback(entity: MatchedEntity) -> bool:
    return not entity.slug and not entity.id


def _district_needs_fallback(entity: MatchedEntity) -> bool:
    return not entity.id


def _describe_metro_fallback(entity: MatchedEntity) -> tuple[list[str], str]:
    """Фолбэк для одной сущности метро: тег-матч, затем расстояние до станции."""
    tag_ids = block_ids_by_tag("metro", entity.name)
    if tag_ids:
        return tag_ids, (
            f"метро «{entity.name}»: id не подтверждён — применена привязка ЖК "
            f"к станции из живых данных pik.ru ({len(tag_ids)} ЖК)"
        )

    ref_entry = find_by_name(load_metro(), entity.name)
    if ref_entry is None or ref_entry.lat is None or ref_entry.lon is None:
        return [], (
            f"метро «{entity.name}»: id не подтверждён, а координаты станции "
            "неизвестны — сузить по ЖК не удалось, фильтр пропущен"
        )

    ids, nearest_m, within_radius = nearby_block_ids(
        ref_entry.lat,
        ref_entry.lon,
        STATION_CLASS_DEFAULT_RADIUS_M,
        STATION_CLASS_FALLBACK_LIMIT,
    )
    if not ids:
        return [], (
            f"метро «{entity.name}»: id не подтверждён, и рядом со станцией нет "
            "ни одного ЖК с координатами — фильтр пропущен"
        )

    radius_km = STATION_CLASS_DEFAULT_RADIUS_M / 1000
    if within_radius:
        note = (
            f"метро «{entity.name}»: id не подтверждён — применено приближение "
            f"по расстоянию, {len(ids)} ЖК в радиусе {radius_km:g} км от станции"
        )
    else:
        nearest_km = (nearest_m or 0.0) / 1000
        note = (
            f"метро «{entity.name}»: id не подтверждён, в радиусе {radius_km:g} км "
            f"от станции ЖК нет — показаны {len(ids)} ближайших, "
            f"от {nearest_km:.2f} км {STRAIGHT_LINE_NOTE}"
        )
    return ids, note


def _describe_tag_only_fallback(
    kind: str, entity: MatchedEntity, field_name: str
) -> tuple[list[str], str]:
    """Фолбэк для округа/района: только точный тег-матч (координат сущности нет)."""
    ids = block_ids_by_tag(field_name, entity.name)
    if ids:
        return ids, (
            f"{kind} «{entity.name}»: id не подтверждён — применена привязка ЖК "
            f"к {field_name} из живых данных pik.ru ({len(ids)} ЖК)"
        )
    return [], (
        f"{kind} «{entity.name}»: id не подтверждён, и ни один ЖК к нему не "
        "привязан в справочнике — фильтр пропущен"
    )


def handled_by_fallback(entity_field: str, entity: MatchedEntity) -> bool:
    """Возьмёт ли geo-фолбэк эту сущность на себя (для подавления warning).

    Используется фасадом ``app.parsing.parser``: предупреждение «не имеет id,
    в ссылку не попадет» стало бы враньём для сущностей, которые этот модуль
    заменяет сужением по ``blocks`` — исход фолбэка (сколько ЖК, каким способом,
    или «фильтр пропущен» с причиной) сообщает СВОЯ заметка через
    ``app.pik.validator.validate``, вторая параллельная формулировка о том же
    самом только противоречила бы ей.
    """
    if entity_field == "metro":
        return _metro_needs_fallback(entity)
    if entity_field == "counties":
        return _county_needs_fallback(entity)
    if entity_field == "districts":
        return _district_needs_fallback(entity)
    return False


def resolve_fallback_block_ids(criteria: Criteria) -> LocationFallback:
    """geo-фолбэк на ``blocks`` для всех локационных сущностей без достоверного id.

    Дедуплицирует id ЖК, сохраняя порядок первого появления (метро -> округа ->
    районы, внутри группы — порядок из ``criteria``). Используется и в
    ``app.pik.url_builder.build_url`` (чтобы ссылка реально что-то фильтровала),
    и в ``app.pik.validator.validate`` (чтобы result_count проверял ровно то же
    сужение, что получит пользователь по ссылке).
    """
    result = LocationFallback()
    seen: set[str] = set()

    def _extend(ids: list[str]) -> None:
        for cid in ids:
            if cid not in seen:
                seen.add(cid)
                result.block_ids.append(cid)

    for entity in criteria.metro:
        if not _metro_needs_fallback(entity):
            continue
        ids, note = _describe_metro_fallback(entity)
        _extend(ids)
        result.notes.append(note)

    for entity in criteria.counties:
        if not _county_needs_fallback(entity):
            continue
        ids, note = _describe_tag_only_fallback("округ", entity, "county")
        _extend(ids)
        result.notes.append(note)

    for entity in criteria.districts:
        if not _district_needs_fallback(entity):
            continue
        ids, note = _describe_tag_only_fallback("район", entity, "district")
        _extend(ids)
        result.notes.append(note)

    # «внутри/за МКАД»: у pik.ru нет такого URL-фильтра — сужаем по blocks
    # (app.geo.mkad.point_in_mkad). Если выше уже собрано сужение по другим
    # локациям — ПЕРЕСЕКАЕМ (AND: «рядом с X И внутри МКАД»); иначе берём весь
    # набор ЖК нужной стороны. Приближённый полигон (см. mkad_ring.json) —
    # честно помечаем как приближение.
    if criteria.within_mkad is not None:
        side = "внутри МКАД" if criteria.within_mkad else "за МКАД"
        mkad_ids = complexes_in_mkad(criteria.within_mkad)
        if result.block_ids:
            mkad_set = set(mkad_ids)
            kept = [cid for cid in result.block_ids if cid in mkad_set]
            result.block_ids = kept
            result.notes.append(
                f"«{side}»: сужение пересечено с полигоном МКАД (приближение) — "
                f"осталось {len(kept)} ЖК"
            )
        elif mkad_ids:
            _extend(mkad_ids)
            result.notes.append(
                f"«{side}»: применено гео-сужение по полигону МКАД (приближение) — "
                f"{len(mkad_ids)} ЖК с координатами нужной стороны"
            )
        else:
            result.notes.append(
                f"«{side}»: ни одного ЖК с координатами нужной стороны — фильтр пропущен"
            )

    return result
