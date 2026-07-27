"""Аудит падежного покрытия ориентиров (`app/reference/landmarks.json`).

ЗАЧЕМ. Имя ориентира ищется нечётким окном (`app/parsing/rules/landmark.py`) с
порогом `SCORE_THRESHOLD` = 88. Русские склонения этот порог НЕ проходят: замер
показывает «финашке»→«финашка» = 85.7, «бауманки»→«бауманка» = 87.5,
«киевскому вокзалу»→«киевский вокзал» = 81.2. Понижать порог нельзя — законные
склонения топонимов имеют такой же и даже МЕНЬШИЙ score, чем ложные срабатывания
(та же линия, что `app/parsing/stopwords.py`). Поэтому склонения лечатся
ЛЕКСИКОЙ: падежные формы дописываются в `aliases`.

ЧТО ДЕЛАЕТ. Для каждой записи справочника генерирует типовые русские падежные
формы её имени и алиасов (косвенные падежи: род./дат./вин./твор./предл.), гоняет
каждую форму через настоящий `_best_match` и показывает те, что НЕ разрешаются в
эту же запись. Плюс проверяет коллизии: предложенная форма не должна давать >= 88
на ЧУЖОЙ slug — такие формы дописывать в справочник запрещено.

ЭТО ИНСТРУМЕНТ, А НЕ ТЕСТ. Морфология здесь генерируется эвристиками и может
давать неграмматичные формы («финашкы»): отчёт ПРЕДЛАГАЕТ, решение — кураторское.
Автогенерация морфологии сознательно не вносится в pytest (хрупко); зафиксированное
покрытие проверяет детерминированный `tests/reference/test_landmark_aliases.py`.

Запуск:
    uv run python scripts/landmark_alias_audit.py
    uv run python scripts/landmark_alias_audit.py --json-out /tmp/landmarks.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from rapidfuzz import fuzz  # noqa: E402

from app.parsing.rules.landmark import (  # noqa: E402
    SCORE_THRESHOLD,
    _best_match,
    _landmark_choices,
)
from app.reference.loader import RefEntry, load_landmarks, normalize  # noqa: E402

DEFAULT_JSON_OUT_PATH = _PROJECT_ROOT / "graphify-out" / "landmark_alias_audit.json"

#: Максимум слов в базе-источнике: окно матчера ограничено 3 словами
#: (`landmark._MAX_WINDOW`), формы длиннее физически недостижимы.
_MAX_SOURCE_WORDS = 3

_VELAR_HUSH = set("кгхжчшщ")

#: Аббревиатуры не склоняются («у МГУ», «около ВДНХ»). Часть видна по ALL-CAPS в
#: `name`, часть живёт только в алиасах строчными — их перечисляем явно.
_LOWERCASE_ABBREVIATIONS = {
    "вшэ",
    "ниу",
    "ниту",
    "ххс",
    "цпкио",
    "ммдц",
    "мисис",
    "миссис",
    "миси",
    "мгюа",
    "мгму",
    "ранхигс",
    "сво",
    "мид",
    "им",
}

#: Существительные на «-ь» женского рода (у мужских другая парадигма).
_SOFT_FEMININE = {"площадь"}

#: Несклоняемые слова, которые морфологической эвристикой не отличить: «Сити»
#: выглядит как мн. ч. на «-и», латинские коды аэропортов — как основа на согласную.
_INDECLINABLE = {"сити", "москва-сити", "вднха"}

#: Мягкие прилагательные: «третий» → «третьего» (обычная парадигма дала бы
#: «третого»). Держим списком — открытых правил для этого класса нет.
_SOFT_ADJECTIVES = {"третий": "треть", "третьего": "треть"}

#: Слова, которые в справочнике уже стоят в косвенном падеже как часть имени
#: («им. Ломоносова», «Плеханова», «нефти и газа») — склонять их повторно нельзя.
_FROZEN_TOKENS = {"и", "им"}

_CASES = ("gen", "dat", "acc", "ins", "pre")


def _is_abbreviation(token: str, caps_tokens: set[str]) -> bool:
    if token in _LOWERCASE_ABBREVIATIONS or token in caps_tokens or token in _INDECLINABLE:
        return True
    # svo/dme/vko — латинские коды аэропортов. Смотрим на ЛЮБУЮ латинскую букву:
    # `normalize()` гомоглифы не схлопывает, и «svo» может прийти смешанным.
    return any("a" <= ch <= "z" for ch in token)


def _noun_number_gender(word: str) -> str:
    """Грубая оценка «род/число» существительного по окончанию."""
    if word.endswith(("ы", "и")):
        return "pl"
    if word.endswith(("а", "я")):
        return "f"
    if word.endswith(("о", "е")):
        return "n"
    if word.endswith("ь"):
        return "f" if word in _SOFT_FEMININE else "m"
    return "m"


def _noun_forms(word: str) -> dict[str, list[str]]:
    """Косвенные падежи существительного (неодушевлённое: вин. = им.)."""
    stem = word[:-1]
    if word.endswith("ия"):
        # «академия» → «академии» (род./дат./предл. совпадают).
        return {
            "gen": [stem + "и"],
            "dat": [stem + "и"],
            "acc": [stem + "ю"],
            "ins": [stem + "ей"],
            "pre": [stem + "и"],
        }
    if word.endswith("а"):
        gen = stem + ("и" if stem and stem[-1] in _VELAR_HUSH else "ы")
        return {
            "gen": [gen],
            "dat": [stem + "е"],
            "acc": [stem + "у"],
            "ins": [stem + "ой"],
            "pre": [stem + "е"],
        }
    if word.endswith("я"):
        return {
            "gen": [stem + "и"],
            "dat": [stem + "е"],
            "acc": [stem + "ю"],
            "ins": [stem + "ей"],
            "pre": [stem + "е"],
        }
    if word.endswith("ь"):
        if word in _SOFT_FEMININE:
            return {
                "gen": [stem + "и"],
                "dat": [stem + "и"],
                "acc": [word],
                "ins": [stem + "ью"],
                "pre": [stem + "и"],
            }
        return {
            "gen": [stem + "я"],
            "dat": [stem + "ю"],
            "acc": [word],
            "ins": [stem + "ем"],
            "pre": [stem + "е"],
        }
    if word.endswith("о"):
        return {
            "gen": [stem + "а"],
            "dat": [stem + "у"],
            "acc": [word],
            "ins": [stem + "ом"],
            "pre": [stem + "е"],
        }
    if word.endswith("е"):
        return {
            "gen": [stem + "я"],
            "dat": [stem + "ю"],
            "acc": [word],
            "ins": [stem + "ем"],
            "pre": [stem + "е"],
        }
    if word.endswith(("ы", "и")):
        # Мн. ч.: род. п. неоднозначен (нулевое окончание «горы»→«гор» vs
        # «-ов» «пруды»→«прудов»), поэтому предлагаем оба варианта.
        return {
            "gen": [stem + "ов", stem],
            "dat": [stem + "ам"],
            "acc": [word],
            "ins": [stem + "ами"],
            "pre": [stem + "ах"],
        }
    # Согласная: мужской род.
    ins = word + ("ем" if word[-1] in "жчшщц" else "ом")
    return {
        "gen": [word + "а"],
        "dat": [word + "у"],
        "acc": [word],
        "ins": [ins],
        "pre": [word + "е"],
    }


#: Полные окончания прилагательных. «-ы» сюда не входит: краткое притяжательное
#: («воробьёвы») неотличимо от мн. ч. существительного («пруды»), поэтому
#: работает только в позиции определения.
_ADJ_SUFFIXES = ("ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие")


def _is_adjective(word: str, is_last: bool) -> bool:
    if word.endswith(_ADJ_SUFFIXES):
        return True
    # Притяжательное краткое мн. ч. («воробьёвы горы») — только не в позиции головы.
    return not is_last and word.endswith("ы")


def _adjective_forms(word: str, agreement: str) -> dict[str, list[str]]:
    """Косвенные падежи прилагательного, согласованного с головой."""
    if word.endswith("ы") and not word.endswith(("ые",)):  # краткое притяжательное
        stem = word[:-1]
        return {
            "gen": [stem + "ых"],
            "dat": [stem + "ым"],
            "acc": [word],
            "ins": [stem + "ыми"],
            "pre": [stem + "ых"],
        }
    if word in _SOFT_ADJECTIVES:
        soft_stem = _SOFT_ADJECTIVES[word]
        return {
            "gen": [soft_stem + "его"],
            "dat": [soft_stem + "ему"],
            "acc": [word],
            "ins": [soft_stem + "им"],
            "pre": [soft_stem + "ем"],
        }
    stem = word[:-2]
    if agreement == "pl" or word.endswith(("ые", "ие")):
        return {
            "gen": [stem + "их"],
            "dat": [stem + "им"],
            "acc": [word],
            "ins": [stem + "ими"],
            "pre": [stem + "их"],
        }
    if agreement == "f" or word.endswith(("ая", "яя")):
        return {
            "gen": [stem + "ой"],
            "dat": [stem + "ой"],
            "acc": [stem + "ую"],
            "ins": [stem + "ой"],
            "pre": [stem + "ой"],
        }
    # После к/г/х и шипящих твор. п. — «-им», не «-ым» («московским», не
    # «московскым»): то же чередование, что у род. п. мн. ч. существительных.
    ins = stem + ("им" if stem and stem[-1] in _VELAR_HUSH else "ым")
    return {
        "gen": [stem + "ого"],
        "dat": [stem + "ому"],
        "acc": [word],
        "ins": [ins],
        "pre": [stem + "ом"],
    }


def generate_case_forms(base: str, caps_tokens: set[str]) -> list[str]:
    """Типовые косвенные формы фразы. Пустой список = фраза не склоняется."""
    tokens = normalize(base).split()
    if not tokens or len(tokens) > _MAX_SOURCE_WORDS:
        return []
    if any(t in _FROZEN_TOKENS for t in tokens):
        return []
    # Аббревиатура в голове (первое слово) делает фразу неизменяемой:
    # «МГУ им. Ломоносова», «РЭУ Плеханова», «НИУ ВШЭ».
    if _is_abbreviation(tokens[0], caps_tokens) or _is_abbreviation(tokens[-1], caps_tokens):
        return []

    head = tokens[-1]
    # Топонимы на «-о» (Царицыно, Сколково, Внуково, Домодедово) в норме
    # несклоняемы; разговорный род. п. («Внукова») в справочнике уже есть
    # кураторски, а генерировать «Внукову»/«Внукове» — только шум.
    if head.endswith("о"):
        return []
    # Фразовые формы генерируем ТОЛЬКО когда все слова перед головой —
    # согласуемые прилагательные («красная площадь», «московский политех»).
    # Иначе голова стоит первой, а хвост — несогласуемое родительное
    # определение («офис Яндекса», «парк Горького», «храм Христа Спасителя»),
    # и склонение последнего слова рождает неграмматичное «офис яндексой».
    if len(tokens) > 1:
        if not all(_is_adjective(t, is_last=False) for t in tokens[:-1]):
            return []
        if head.endswith(_ADJ_SUFFIXES):
            return []  # эллипсис («городской педагогический») — головы нет
    agreement = _noun_number_gender(head)
    per_token: list[dict[str, list[str]]] = []
    for i, token in enumerate(tokens):
        is_last = i == len(tokens) - 1
        if is_last:
            per_token.append(_noun_forms(token))
        elif _is_adjective(token, is_last):
            per_token.append(_adjective_forms(token, agreement))
        else:
            per_token.append({case: [token] for case in _CASES})

    forms: list[str] = []
    for case in _CASES:
        variants: list[list[str]] = [t[case] for t in per_token]
        # Декартово произведение нужно только там, где падеж дал несколько
        # вариантов (род. п. мн. ч.) — обычно это одна строка.
        combos: list[list[str]] = [[]]
        for options in variants:
            combos = [[*combo, opt] for combo in combos for opt in options]
        forms.extend(" ".join(c) for c in combos)
    return [f for f in dict.fromkeys(forms) if f != normalize(base)]


@dataclass
class FormVerdict:
    form: str
    status: str  # "missing" | "collision"
    #: Ближайшая строка справочника и её оценки — независимо от порога, иначе
    #: у всех непокрытых форм в отчёте был бы score 0 и было бы непонятно,
    #: насколько форма «недобрала» (гейт — WRatio >= 88 И QRatio >= 88).
    best_choice: str = ""
    best_slug: str = ""
    best_wratio: float = 0.0
    best_qratio: float = 0.0


def _nearest(form: str, choices: list[tuple[str, RefEntry]]) -> FormVerdict:
    """Ближайшая строка справочника к форме — без учёта порога."""
    best_choice, best_entry, best_w = "", None, -1.0
    for choice, entry in choices:
        score = fuzz.WRatio(form, choice)
        if score > best_w:
            best_choice, best_entry, best_w = choice, entry, score
    return FormVerdict(
        form=form,
        status="missing",
        best_choice=best_choice,
        best_slug=best_entry.slug if best_entry else "—",
        best_wratio=round(best_w, 1),
        best_qratio=round(fuzz.QRatio(form, best_choice), 1),
    )


@dataclass
class EntryAudit:
    name: str
    slug: str
    missing: list[FormVerdict] = field(default_factory=list)
    collisions: list[FormVerdict] = field(default_factory=list)


@dataclass
class AuditReport:
    total_entries: int
    total_forms: int
    deficient_entries: int
    missing_forms: int
    collision_forms: int
    entries: list[EntryAudit]


def _eponym_surnames(name: str) -> set[str]:
    """Фамилии из «им. X» — они уже стоят в род. п. и не склоняются повторно.

    «МГЮА им. Кутафина» → алиас «кутафина» источником брать нельзя: ж. р. на
    «-а» дал бы «кутафины»/«кутафиной». Берём из данных, а не списком.
    """
    tokens = normalize(name).replace(".", " ").split()
    return {tokens[i + 1] for i, t in enumerate(tokens) if t == "им" and i + 1 < len(tokens)}


def _sources(entry_strings: list[str], caps_tokens: set[str], frozen_names: set[str]) -> list[str]:
    """Базы для генерации: только «именительные» строки.

    Алиас, который САМ получается склонением другой строки записи («бауманки» из
    «бауманка»), источником не берём — склонять уже склонённое бессмысленно.
    """
    derived: set[str] = set()
    for base in entry_strings:
        derived.update(generate_case_forms(base, caps_tokens))
    # Кураторский разговорный род. п. несклоняемого топонима («Внукова» при
    # «Внуково») тоже не источник: он выглядит как ж. р. на «-а» и дал бы
    # «Внуковы»/«Внуковой». Узнаём по общей основе с формой на «-о».
    frozen_stems = {normalize(base)[:-1] for base in entry_strings if normalize(base).endswith("о")}
    return [
        s
        for s in entry_strings
        if normalize(s) not in derived
        and normalize(s)[:-1] not in frozen_stems
        and normalize(s) not in frozen_names
    ]


def run_audit() -> AuditReport:
    landmarks = load_landmarks()
    choices = _landmark_choices()
    caps_tokens = {
        token.casefold().strip(".")
        for entry in landmarks
        for token in entry.name.split()
        if token.isupper()
    }
    own_choice_slugs: dict[str, set[str]] = {}
    for choice, entry in choices:
        own_choice_slugs.setdefault(choice, set()).add(entry.slug)

    entries: list[EntryAudit] = []
    total_forms = 0
    for entry in landmarks:
        strings = [entry.name, *entry.aliases]
        known = {normalize(s) for s in strings}
        audit = EntryAudit(name=entry.name, slug=entry.slug)
        for base in _sources(strings, caps_tokens, _eponym_surnames(entry.name)):
            for form in generate_case_forms(base, caps_tokens):
                if form in known:
                    continue
                known.add(form)
                total_forms += 1
                match = _best_match(form, choices)
                if match is not None and match[0].slug == entry.slug:
                    continue
                verdict = _nearest(form, choices)
                # Коллизия: форма уже принадлежит чужой записи (точно или
                # нечётко >= порога). Такую форму дописывать НЕЛЬЗЯ.
                foreign_exact = own_choice_slugs.get(form, set()) - {entry.slug}
                foreign_fuzzy = match is not None and match[0].slug != entry.slug
                if foreign_exact or foreign_fuzzy:
                    verdict.status = "collision"
                    audit.collisions.append(verdict)
                else:
                    audit.missing.append(verdict)
        if audit.missing or audit.collisions:
            entries.append(audit)

    return AuditReport(
        total_entries=len(landmarks),
        total_forms=total_forms,
        deficient_entries=sum(1 for e in entries if e.missing),
        missing_forms=sum(len(e.missing) for e in entries),
        collision_forms=sum(len(e.collisions) for e in entries),
        entries=entries,
    )


def _print_report(report: AuditReport) -> None:
    print("=" * 100)
    print(f"АУДИТ ПАДЕЖНОГО ПОКРЫТИЯ ОРИЕНТИРОВ — порог {SCORE_THRESHOLD}, офлайн")
    print("=" * 100)
    for audit in report.entries:
        print(f"\n[{audit.slug}] {audit.name}")
        for verdict in audit.missing:
            print(
                f"     НЕ РАСПОЗНАНА: {verdict.form!r} → ближайшее "
                f"{verdict.best_choice!r} [{verdict.best_slug}] "
                f"W={verdict.best_wratio} Q={verdict.best_qratio}"
            )
        for verdict in audit.collisions:
            print(
                f"     КОЛЛИЗИЯ (не добавлять): {verdict.form!r} → "
                f"{verdict.best_choice!r} [{verdict.best_slug}] "
                f"W={verdict.best_wratio} Q={verdict.best_qratio}"
            )
    print("\n" + "-" * 100)
    print("ИТОГО")
    print("-" * 100)
    print(f"Записей в справочнике: {report.total_entries}")
    print(f"Сгенерировано форм: {report.total_forms}")
    print(f"Дефицитных записей: {report.deficient_entries} из {report.total_entries}")
    print(f"Форм не распознаётся: {report.missing_forms}")
    print(f"Форм с коллизией (добавлять запрещено): {report.collision_forms}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT_PATH)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = run_audit()
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), "utf-8")
    if not args.quiet:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Ручная утилита: точечный замер score формы против справочника (для протокола
# решений «почему лексика, а не порог»).
def measure(form: str) -> list[tuple[str, float]]:
    choices = _landmark_choices()
    scored = [(c, fuzz.WRatio(normalize(form), c)) for c, _e in choices]
    return sorted(scored, key=lambda p: -p[1])[:5]
