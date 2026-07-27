"""Инварианты справочника ориентиров ``landmarks.json`` и его падежного покрытия.

КОНТЕКСТ. Имя ориентира ищется нечётким окном (``app/parsing/rules/landmark.py``)
с порогом ``SCORE_THRESHOLD`` = 88, и русские склонения этот порог НЕ проходят:
«финашке»→«финашка» = 85.7, «бауманки»→«бауманка» = 87.5, «киевскому
вокзалу»→«киевский вокзал» = 81.2. Понижать порог нельзя — законные склонения
топонимов имеют такой же и даже МЕНЬШИЙ score, чем ложные срабатывания (та же
линия, что ``app/parsing/stopwords.py``). Поэтому склонения лечатся ЛЕКСИКОЙ:
падежные формы дописаны в ``aliases``.

ЗАЧЕМ ЭТОТ ТЕСТ. ``aliases`` — кураторское поле, и его легко откатить «причёсыванием»
справочника: тогда «ближайшая к Финашке» снова молча перестанет распознаваться,
ориентир не даст координат, ``blocks=`` из ссылки исчезнет — и никакой тест этого
не заметит. Снимок ``CASE_FORMS`` ниже фиксирует достигнутое покрытие пофамильно.

ПОЧЕМУ ТАБЛИЦА ЯВНАЯ, А НЕ ГЕНЕРАТОР. Морфология генерируется эвристиками
(``scripts/landmark_alias_audit.py``) и на редких классах даёт неграмматичные
формы. Инструмент ПРЕДЛАГАЕТ формы, кураторское решение попадает сюда списком:
тест обязан быть детерминированным, а не флапать вместе с эвристикой.
"""

import pytest

from app.parsing.rules.landmark import SCORE_THRESHOLD, _landmark_choices
from app.reference.loader import load_landmarks, normalize

#: Падежные формы, которые ОБЯЗАНЫ разрешаться в свою запись. Заполнено по
#: результатам ``uv run python scripts/landmark_alias_audit.py`` (аудит показал
#: 91 непокрытых форм у 28 записей из 50; после правки данных — 0).
CASE_FORMS: dict[str, tuple[str, ...]] = {
    "МГУ им. Ломоносова": (
        "московского государственного университета",
        "московскому государственному университету",
    ),
    "Высшая школа экономики": (
        "вышки",
        "вышке",
        "вышку",
        "вышкой",
    ),
    "МФТИ": ("физтехом",),
    "Финансовый университет": (
        "финансового университета",
        "финансовому университету",
        "финашки",
        "финашке",
        "финашку",
        "финашкой",
    ),
    "Сеченовский университет": (
        "сеченовского университета",
        "сеченовскому университету",
        "первого меда",
        "первому меду",
        "первым медом",
        "первом меде",
        "сеченовкой",
    ),
    "РЭУ им. Плеханова": ("плехановкой",),
    "Московский Кремль": (
        "московского кремля",
        "московскому кремлю",
        "московским кремлем",
        "московском кремле",
        "кремлем",
        "кремле",
    ),
    "Красная площадь": ("красную площадь",),
    "Останкинская телебашня": (
        "останкинскую телебашню",
        "телебашне",
        "телебашней",
    ),
    "Большой театр": (
        "большому театру",
        "большим театром",
    ),
    "Третьяковская галерея": (
        "третьяковской галереи",
        "третьяковской галерее",
        "третьяковскую галерею",
        "третьяковской галереей",
        "третьяковкой",
    ),
    "Стадион Лужники": (
        "лужникам",
        "лужниками",
        "лужниках",
    ),
    "Парк Зарядье": (
        "зарядья",
        "зарядью",
    ),
    "Парк Сокольники": ("сокольников",),
    "Патриаршие пруды": (
        "патриков",
        "патрикам",
        "патриками",
    ),
    "Москва-Сити": (
        "делового центра",
        "деловому центру",
        "деловым центром",
    ),
    "Штаб-квартира Яндекса": ("яндексом",),
    "Штаб-квартира Сбербанка": (
        "сбербанком",
        "сбером",
    ),
    "Центральный офис Газпрома": ("газпромом",),
    "МАИ": (
        "авиационного института",
        "авиационному институту",
        "авиационном институте",
        "московского авиационного института",
        "московскому авиационному институту",
        "московском авиационном институте",
    ),
    "МЭИ": (
        "энергетического института",
        "энергетическому институту",
        "московского энергетического института",
        "московскому энергетическому институту",
        "московском энергетическом институте",
        "энергетическом институте",
    ),
    "МПГУ": (
        "ленинского педа",
        "ленинскому педу",
        "ленинским педом",
        "ленинском педе",
    ),
    "РАНХиГС": (
        "президентской академии",
        "президентскую академию",
        "президентской академией",
    ),
    "МГЮА им. Кутафина": (
        "юридической академии",
        "юридическую академию",
        "юридической академией",
        "юридического университета",
        "юридическому университету",
    ),
    "МГЛУ": ("инязом",),
    "РГУ нефти и газа им. Губкина": ("керосинкой",),
    "МГМСУ им. Евдокимова": (
        "третьего меда",
        "третьему меду",
        "третьим медом",
        "третьем меде",
    ),
    "Московский политех": (
        "московского политеха",
        "московскому политеху",
        "московским политехом",
        "московском политехе",
        "московского политехнического университета",
        "московскому политехническому университету",
        "московском политехническом университете",
    ),
}

#: Коллизии алиасов, существующие в справочнике ИЗНАЧАЛЬНО (не внесены падежной
#: правкой). Обе — пары настоящих вузовских аббревиатур, отличающихся одной
#: буквой, обе дают ровно 88.9 при пороге 88:
#:   * «мисис» (НИТУ МИСИС) vs «миси» (МГСУ — историческое имя МИСИ);
#:   * «мгсу» (МГСУ) vs «мгмсу» (МГМСУ им. Евдокимова).
#: Лечить их порогом нельзя (см. докстринг модуля), а выкидывать алиас — значит
#: терять живое обиходное имя вуза. Держим как задокументированное исключение:
#: НОВЫЕ алиасы не имеют права добавить сюда ни одной пары.
KNOWN_ALIAS_COLLISIONS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"мисис", "миси"}),
        frozenset({"мгсу", "мгмсу"}),
    }
)

_CASE_FORM_CASES = [(form, name) for name, forms in CASE_FORMS.items() for form in forms]


@pytest.mark.parametrize(("form", "expected"), _CASE_FORM_CASES)
def test_curated_case_forms_resolve(form: str, expected: str) -> None:
    """Каждая зафиксированная падежная форма даёт ровно свой ориентир."""
    from app.parsing.rules.landmark import extract_landmark_requirements

    reqs, _spans = extract_landmark_requirements(f"квартира рядом с {form}")

    assert [r.name for r in reqs] == [expected], (
        f"Форма {form!r} перестала разрешаться в {expected!r} — вероятно, из "
        "landmarks.json пропал падежный алиас (см. докстринг модуля)"
    )


def test_curated_forms_are_actually_in_aliases() -> None:
    """Таблица снимка не рассинхронизировалась с содержимым справочника.

    Симметрично ``test_known_allowlist_entries_still_exist`` в
    ``test_metro_integrity.py``: форма может разрешаться «случайно» (нечёткое
    попадание в соседний алиас), и тогда снимок перестаёт что-либо охранять.
    """
    by_name = {e.name: e for e in load_landmarks()}

    missing: list[tuple[str, str]] = []
    for name, forms in CASE_FORMS.items():
        assert name in by_name, f"Записи {name!r} больше нет в landmarks.json"
        aliases = {normalize(a) for a in by_name[name].aliases}
        missing.extend((name, form) for form in forms if normalize(form) not in aliases)

    assert missing == [], f"Формы из снимка отсутствуют в aliases: {missing}"


class TestRequiredFields:
    """Инвариант CLAUDE.md: у ориентира обязательны ``lat``/``lon``/``category``.

    Координаты — единственное, зачем ориентир вообще нужен (гео-сужение ЖК по
    haversine). Запись без них молча выпадает из
    ``extract_landmark_requirements``, и требование «рядом с X» исчезает.
    """

    def test_every_entry_has_coordinates_and_category(self) -> None:
        offenders = [
            e.name for e in load_landmarks() if e.lat is None or e.lon is None or not e.category
        ]
        assert offenders == [], f"Записи landmarks.json без lat/lon/category: {offenders}"

    def test_category_is_from_known_set(self) -> None:
        allowed = {"university", "employer", "landmark"}
        offenders = {e.category for e in load_landmarks()} - allowed
        assert offenders == set(), f"Неизвестные категории ориентиров: {offenders}"


class TestNoDuplicates:
    """Дубли имени/slug/id ломают однозначность матчинга и ссылки."""

    def test_no_duplicate_normalized_names(self) -> None:
        seen: dict[str, str] = {}
        duplicates: list[tuple[str, str]] = []
        for entry in load_landmarks():
            key = normalize(entry.name)
            if key in seen:
                duplicates.append((seen[key], entry.name))
            else:
                seen[key] = entry.name

        assert duplicates == [], f"Дубли по нормализованному имени: {duplicates}"

    def test_no_duplicate_slugs(self) -> None:
        slugs = [e.slug for e in load_landmarks() if e.slug]
        assert len(slugs) == len(set(slugs)), "Повторяющийся slug у разных ориентиров"

    def test_no_duplicate_ids(self) -> None:
        ids = [e.id for e in load_landmarks() if e.id]
        assert len(ids) == len(set(ids)), "Повторяющийся id у разных ориентиров"


class TestNoAliasCollisions:
    """Одна строка не должна разрешаться в ДВА разных ориентира.

    Коллизия означает, что запрос «рядом с X» сузит ЖК по чужим координатам —
    молча и неотличимо от правильного ответа.
    """

    def test_no_choice_string_belongs_to_two_slugs(self) -> None:
        """Точный дубль: одна и та же нормализованная строка у двух записей."""
        owners: dict[str, set[str]] = {}
        for choice, entry in _landmark_choices():
            owners.setdefault(choice, set()).add(entry.slug)

        shared = {c: sorted(s) for c, s in owners.items() if len(s) > 1}
        assert shared == {}, f"Строка справочника принадлежит двум ориентирам: {shared}"

    def test_no_alias_collision_between_entries(self) -> None:
        """Нечёткая коллизия: строка чужой записи проходит гейт (WRatio И QRatio).

        Гейт двойной — ``landmark._best_match`` требует и ``WRatio >= 88``, и
        ``QRatio >= 88``: одного WRatio мало («сбером»→«сбер» W=90, но Q=80).
        """
        from rapidfuzz import fuzz

        choices = _landmark_choices()
        collisions: set[frozenset[str]] = set()
        for i, (left, left_entry) in enumerate(choices):
            for right, right_entry in choices[i + 1 :]:
                if left_entry.slug == right_entry.slug:
                    continue
                if (
                    fuzz.WRatio(left, right) >= SCORE_THRESHOLD
                    and fuzz.QRatio(left, right) >= SCORE_THRESHOLD
                ):
                    collisions.add(frozenset({left, right}))

        unexpected = sorted(tuple(sorted(c)) for c in collisions - KNOWN_ALIAS_COLLISIONS)
        assert unexpected == [], (
            f"Новые коллизии алиасов ориентиров: {unexpected}. Такую форму дописывать "
            "в landmarks.json нельзя — она сузит ЖК по координатам чужого ориентира."
        )

    def test_allowlisted_collisions_still_exist(self) -> None:
        """Allowlist не рассинхронизировался: исключения — про живые данные.

        Если пара исчезла (алиас переименован/удалён), исключение обязано уйти
        из allowlist-а вместе с ней, иначе оно начнёт прикрывать будущую
        настоящую коллизию с теми же строками.
        """
        strings = {choice for choice, _entry in _landmark_choices()}

        stale = [sorted(pair) for pair in KNOWN_ALIAS_COLLISIONS if not pair <= strings]
        assert stale == [], f"Пары из KNOWN_ALIAS_COLLISIONS больше нет в landmarks.json: {stale}"
