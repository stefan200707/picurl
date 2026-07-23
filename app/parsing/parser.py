"""Parse facade: parse(text) -> Criteria + warnings."""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from app.parsing.entity_match import match_entities
from app.parsing.rules import Span, apply_rules
from app.parsing.rules.core import _normalize as _normalize_chars
from app.parsing.schema import Criteria, MatchedEntity
from app.parsing.stopwords import STOP_WORDS

#: Максимум значимых слов в нераспознанном фрагменте, чтобы считать его
#: кандидатом на «это может быть опция/группа опций, которую rapidfuzz не
#: сматчил». Отсекает длинные куски-мусор (перечисления, свободный текст) —
#: фразы-синонимы фильтров коротки («отдельный санузел», «своя ванная»).
MAX_OPTION_CANDIDATE_WORDS = 4

#: Словоформы маркеров близости (синхронизировано с
#: ``rules.core._PROXIMITY_MARKER``; ё уже нормализована в е) и слова-носители
#: локации. Фрагмент, целиком состоящий из них, — геохвост пространственной
#: конструкции, а не кандидат в опции (Milestone AI-20, Фикс 4).
_PROXIMITY_CHUNK_WORDS = frozenset(
    {
        # формы маркера близости
        "рядом",
        "поближе",
        "ближе",
        "недалеко",
        "неподалеку",
        "поблизости",
        "вблизи",
        "близко",
        "около",
        "возле",
        "у",
        "к",
        "ко",
        "с",
        "со",
        "от",
        # слова-носители локации
        "метро",
        "м",
        "станция",
        "станции",
        "станций",
        "ветка",
        "ветки",
        "ветке",
        "веток",
        "линия",
        "линии",
        "линий",
        "район",
        "района",
        "районе",
        "районов",
        "округ",
        "округа",
        "округе",
        "округов",
        "жк",
    }
)


@dataclass
class ParseResult:
    """Результат работы фасада парсера.

    Помимо ``criteria`` и ``warnings`` несёт ``option_candidates`` — структурный
    список коротких нераспознанных фрагментов, которые могут оказаться
    опцией/группой опций (rapidfuzz их не сматчил по строковому сходству). Их
    добивает ИИ-резолвинг (:func:`app.ai.enrichment.resolve_options`).

    Для обратной совместимости с ``criteria, warnings = parse(text)`` итерация по
    результату выдаёт ровно два элемента (criteria, warnings); фрагменты
    достаются только по имени поля ``result.option_candidates``.
    """

    criteria: Criteria
    warnings: list[str]
    option_candidates: list[str] = field(default_factory=list)

    def __iter__(self) -> Iterator:
        return iter((self.criteria, self.warnings))


def _merge_spans(spans: list[Span]) -> list[Span]:
    """Схлопывает перекрывающиеся диапазоны."""
    if not spans:
        return []
    sorted_spans = sorted(spans, key=lambda s: s[0])
    merged = [sorted_spans[0]]
    for current in sorted_spans[1:]:
        prev = merged[-1]
        if current[0] <= prev[1]:
            # Overlapping or adjacent
            merged[-1] = (prev[0], max(prev[1], current[1]))
        else:
            merged.append(current)
    return merged


def _chunk_words(text_chunk: str) -> list[str]:
    """Лексемы куска после посимвольной нормализации (гомоглифы → кириллица).

    Латинская «c» (визуальный дубль кириллической «с») до Milestone AI-20
    проходила проверку стоп-слов как «значимое слово» и порождала мусорный
    warning; нормализация через ``rules.core._normalize`` закрывает весь класс
    подменённых раскладкой букв разом.
    """
    normalized = _normalize_chars(text_chunk)
    # Оставляем буквы, цифры и дефисные слова целиком (напр. «кв-ра»)
    return re.findall(r"[а-яёa-z0-9]+(?:-[а-яёa-z0-9]+)*", normalized)


def _is_significant(text_chunk: str) -> bool:
    """Проверяет, содержит ли нераспознанный кусок значимую информацию."""
    words = _chunk_words(text_chunk)
    if not words:
        return False
    # Если остались только стоп-слова, кусок незначимый
    significant_words = [w for w in words if w not in STOP_WORDS]
    return len(significant_words) > 0


def _missing_id_warning(
    entity_field: str, entity_name: str, ent: MatchedEntity, total_locations: int
) -> str | None:
    """Сформировать warning о недостающей URL-форме сущности (AUDIT_REPORT 2.7).

    Правило single-путь / multi-query: при нескольких локациях всё уходит в
    query по ``id``; при единственной локации район/ЖК всё равно требуют ``id``
    (пути «рядом с районом/ЖК» нет), а метро/округ могут пойти в путь по
    ``slug``. Возвращает текст предупреждения либо ``None``, если у сущности
    есть нужная форма.
    """
    needs_id_only = total_locations > 1 or entity_field in ("districts", "complexes")
    if needs_id_only:
        if not ent.id:
            return f'{entity_name} "{ent.name}" не имеет id, в ссылку не попадет'
        return None
    if not ent.slug and not ent.id:
        return f'{entity_name} "{ent.name}" не имеет slug или id, в ссылку не попадет'
    return None


def _looks_like_option(text_chunk: str) -> bool:
    """Похож ли нераспознанный фрагмент на опцию/группу опций (кандидат для ИИ).

    Эвристика для отсечения совсем не относящегося шума: значимые слова (не
    стоп-слова) не длиннее :data:`MAX_OPTION_CANDIDATE_WORDS` и есть хотя бы одно
    буквенное слово (даты/голые числа отбрасываем — под фильтр-опцию они не
    похожи). Заведомо неподдерживаемые pik.ru фразы (категория 24) сюда не
    попадают: их спаны уже помечены «понятыми» в apply_rules.
    """
    words = _chunk_words(text_chunk)
    # Геохвост («недалеко от метро», «возле округа») — фрагмент целиком из
    # маркеров близости, слов-носителей локации и стоп-слов. Это остаток
    # пространственной конструкции, а не фраза-синоним опции: в кандидаты для
    # ИИ-резолвера он не идёт (Milestone AI-20, Фикс 4) — раньше каждый такой
    # хвост порождал лишний вызов модели (и падал в 429 на ровном месте).
    if words and all(
        w in STOP_WORDS or w in _PROXIMITY_CHUNK_WORDS or w.startswith("ближайш") for w in words
    ):
        return False
    significant = [w for w in words if w not in STOP_WORDS]
    if not significant or len(significant) > MAX_OPTION_CANDIDATE_WORDS:
        return False
    # Нужна хотя бы одна буквенная (не чисто числовая) значимая лексема.
    return any(re.search(r"[а-яёa-z]", w) for w in significant)


def parse(text: str) -> ParseResult:
    """Единая точка входа парсинга.

    Извлекает структурные факты и сущности, собирает их в Criteria.
    Всё нераспознанное или неподдерживаемое отправляет в warnings.
    """
    # 1. Прогоняем регулярные правила
    rules_outcome = apply_rules(text)
    criteria = rules_outcome.criteria
    consumed = rules_outcome.consumed.copy()
    warnings = []

    # Неподдерживаемые фичи ("вторичка") -> warnings
    for unsupp_text, reason in rules_outcome.unsupported:
        warnings.append(f"«{unsupp_text}»: {reason}")

    # Очистка текста от структурных правил (заменяем пробелами, чтобы сохранить индексы)
    # Это предотвращает попадание кусков правил в скользящее окно матчера
    text_list = list(text)
    for start, end in rules_outcome.consumed:
        text_list[start:end] = [" "] * (end - start)
    clean_text = "".join(text_list)

    # 2. Прогоняем матчинг сущностей по очищенному тексту
    matches, entity_warnings = match_entities(clean_text)
    warnings.extend(entity_warnings)

    # 3. Добавляем найденные сущности в Criteria
    seen_entity_ids = set()

    def _is_overlap(s1: Span, s2: Span) -> bool:
        return max(s1[0], s2[0]) < min(s1[1], s2[1])

    for match in matches:
        # Проверяем, не перекрывается ли сущность с уже распознанными правилами
        if any(_is_overlap(match.span, used) for used in rules_outcome.consumed):
            continue

        consumed.append(match.span)
        # Дедупликация
        entity_key = (match.type, match.entity.name)
        if entity_key in seen_entity_ids:
            continue
        seen_entity_ids.add(entity_key)

        if match.type == "metro":
            criteria.metro.append(match.entity)
        elif match.type == "county":
            criteria.counties.append(match.entity)
        elif match.type == "district":
            criteria.districts.append(match.entity)
        elif match.type == "complex":
            criteria.complexes.append(match.entity)
        elif match.type == "option_groups" and match.entity.slug:
            criteria.option_groups.append(match.entity.slug)
        elif match.type == "options" and match.entity.slug:
            criteria.options.append(match.entity.slug)

    # Fallback-шаблон для нераспознанных гео-маркеров (метро)
    for fm_name, fm_span in rules_outcome.fallback_metro:
        if not any(_is_overlap(fm_span, used) for used in consumed):
            warnings.append(f'Станция метро "{fm_name}" не найдена в базе, пропущена')
            consumed.append(fm_span)

    # Проверка на наличие id для мульти-выбора локаций
    total_locations = (
        len(criteria.metro)
        + len(criteria.counties)
        + len(criteria.districts)
        + len(criteria.complexes)
    )
    if total_locations > 0:
        fields = [
            ("metro", "Метро"),
            ("counties", "Округ"),
            ("districts", "Район"),
            ("complexes", "ЖК"),
        ]
        # Импорт здесь, а не в шапке: parser — верхний слой пайплайна, тянуть
        # pik/geo при импорте модуля не хочется; функция чистая и лёгкая.
        from app.pik.location_fallback import handled_by_fallback

        for entity_field, entity_name in fields:
            for ent in getattr(criteria, entity_field):
                warning = _missing_id_warning(entity_field, entity_name, ent, total_locations)
                # Сущности без достоверного id, которые geo-фолбэк заменит
                # сужением по blocks (Milestone AI-20), предупреждения «в ссылку
                # не попадет» не получают — это больше не правда; исход фолбэка
                # сообщает своя заметка через validator.
                if warning and handled_by_fallback(entity_field, ent):
                    warning = None
                if warning:
                    warnings.append(warning)

    # 4. Вычисляем нераспознанные куски текста
    merged_consumed = _merge_spans(consumed)

    unconsumed_spans = []
    current_pos = 0
    for start, end in merged_consumed:
        if start > current_pos:
            unconsumed_spans.append((current_pos, start))
        current_pos = max(current_pos, end)

    if current_pos < len(text):
        unconsumed_spans.append((current_pos, len(text)))

    option_candidates: list[str] = []
    for start, end in unconsumed_spans:
        chunk = text[start:end]
        # Разбиваем нераспознанный текст по знакам препинания и союзам, чтобы
        # давать более точные предупреждения. Точку внутри числа («1.5») не
        # считаем разделителем — иначе дробь ломается на «1» и «5».
        subchunks = re.split(r"[,;]|(?<!\d)\.(?!\d)|\s+и\s+|\s+а\s+|\s+но\s+", chunk)
        for subchunk in subchunks:
            if _is_significant(subchunk):
                cleaned_chunk = subchunk.strip(" ,.-:;!?")
                if cleaned_chunk:
                    warnings.append(f"«{cleaned_chunk}»: не удалось распознать, не попало в ссылку")
                    # Короткий нераспознанный фрагмент — кандидат на «это опция,
                    # которую rapidfuzz не сматчил по буквам». Добивает ИИ.
                    if _looks_like_option(cleaned_chunk):
                        option_candidates.append(cleaned_chunk)

    return ParseResult(criteria=criteria, warnings=warnings, option_candidates=option_candidates)
