"""Офлайн-аудит парсера: прогон корпуса запросов без сети и без ИИ.

Замкнутый контур для поиска дефектов КЛАССАМИ, а не по одному живому запросу.
Читает `tests/corpus/queries.txt`, для каждой строки зовёт только
``app.parsing.parser.parse`` и ``app.pik.url_builder.build_url`` — никаких
обращений к ``app.ai.enrichment.enrich`` (ИИ) и ``app.pik.validator.validate``
(сеть). Пайплайн `parse -> build_url` уже сам по себе полностью офлайн
(см. CLAUDE.md, инвариант «базовый пайплайн не использует LLM/внешние API в
рантайме»), так что этот скрипт просто гоняет его по большому корпусу и
агрегирует симптомы.

Куда пишется машинный отчёт: по умолчанию ``graphify-out/parse_audit.json``.
Решение (не scratchpad): каталог `graphify-out/` уже гитигнорится и служит
местом для сгенерированных проектных артефактов (граф, отчёты) — это тот же
класс файла, что и остальное в этой директории, и он останется в репозитории
между запусками (в отличие от scratchpad сессии агента), что удобно для
диффа «стало лучше/хуже» между прогонами после исправлений.

Запуск::

    uv run python -m scripts.parse_audit
    uv run python scripts/parse_audit.py
    uv run python -m scripts.parse_audit --limit 30 --only-warnings
    uv run python -m scripts.parse_audit --json-out /tmp/audit.json --quiet

Скрипт ничего не меняет в `app/` и не является частью рантайм-пайплайна —
это инструмент разработчика для регрессионного контроля качества парсинга.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Корень проекта — родитель каталога scripts/. Добавляем в sys.path, чтобы
# скрипт работал и как `python scripts/parse_audit.py` (без установки пакета
# в editable-режиме), и как `python -m scripts.parse_audit`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.parsing.parser import parse  # noqa: E402
from app.pik.url_builder import build_url  # noqa: E402

#: Путь к корпусу запросов по умолчанию.
DEFAULT_CORPUS_PATH = _PROJECT_ROOT / "tests" / "corpus" / "queries.txt"

#: Путь к машинному отчёту по умолчанию (см. докстринг модуля про выбор места).
DEFAULT_JSON_OUT_PATH = _PROJECT_ROOT / "graphify-out" / "parse_audit.json"

#: URL без единого сегмента пути и без query-параметров — «фильтров вообще нет».
_BASE_URL = "https://www.pik.ru/search"

#: Warning вида `«фрагмент»: не удалось распознать, не попало в ссылку` — из
#: него достаём сам нераспознанный фрагмент текста (см. app/parsing/parser.py).
_UNRECOGNIZED_RE = re.compile(r"«(?P<phrase>.+?)»: не удалось распознать, не попало в ссылку")

#: Любой warning с фрагментом в кавычках «...» — используется для нормализации
#: warning'а в шаблон класса дефекта (сам фрагмент заменяется плейсхолдером).
_QUOTED_RE = re.compile(r"«[^»]*»")

#: Порог «почти пустых критериев»: если в тексте значимых слов (без стоп-слов
#: STOP_WORDS парсера) больше этого числа, а to_public_dict() пуст — запрос
#: попадает в отчёт (a). Отдельно от «формально пустых» (ровно 0 полей).
_NEAR_EMPTY_WORD_THRESHOLD = 3

#: Длина «сироты» — нераспознанный фрагмент из 1-2 значащих символов (после
#: снятия пробелов/пунктуации), см. пункт (c) задания.
_ORPHAN_MAX_LEN = 2

#: Сколько самых частых нераспознанных фраз показывать (пункт d задания).
_TOP_PHRASES_LIMIT = 30

#: Сколько классов дефектов показывать в консольном отчёте.
_TOP_CLASSES_LIMIT = 20


@dataclass
class QueryResult:
    """Результат прогона одного запроса корпуса через parse -> build_url."""

    text: str
    criteria: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    option_candidates: list[str] = field(default_factory=list)
    url: str = ""
    uncovered_chars: int = 0
    total_chars: int = 0
    coverage_ratio: float = 1.0
    crashed: bool = False
    error_type: str = ""
    error_message: str = ""
    traceback_line: str = ""


def _load_corpus(path: Path, limit: int | None) -> list[str]:
    """Читает корпус: строки без комментариев (`#`) и без пустых строк."""
    lines = path.read_text(encoding="utf-8").splitlines()
    queries = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    if limit is not None:
        queries = queries[:limit]
    return queries


def _significant_word_count(text: str) -> int:
    """Число «значимых» слов в тексте (грубая оценка, без импорта STOP_WORDS).

    Намеренно не тянет ``app.parsing.stopwords`` — цель этой метрики просто
    отличить «пара служебных слов» от «содержательного текста», а не в
    точности повторить логику парсера (та уже отражена в самих warnings).
    """
    words = re.findall(r"[а-яёa-z0-9]+", text.lower())
    return len(words)


def _run_one(text: str) -> QueryResult:
    """Прогоняет один запрос через parse() и build_url() без сети и без ИИ."""
    result = QueryResult(text=text, total_chars=len(text))
    try:
        parse_result = parse(text)
    except Exception as exc:  # аудит обязан пережить любой сбой parse()
        tb_lines = traceback.format_exception_only(type(exc), exc)
        result.crashed = True
        result.error_type = f"parse:{type(exc).__name__}"
        result.error_message = str(exc)
        result.traceback_line = tb_lines[-1].strip() if tb_lines else repr(exc)
        return result

    criteria = parse_result.criteria
    warnings = list(parse_result.warnings)
    result.warnings = warnings
    result.option_candidates = list(parse_result.option_candidates)
    result.criteria = criteria.to_public_dict()

    uncovered = sum(len(m.group("phrase")) for m in _UNRECOGNIZED_RE.finditer("\n".join(warnings)))
    result.uncovered_chars = uncovered
    result.coverage_ratio = (
        1.0 if result.total_chars == 0 else max(0.0, 1.0 - uncovered / result.total_chars)
    )

    try:
        result.url = build_url(criteria)
    except Exception as exc:  # то же самое для build_url()
        tb_lines = traceback.format_exception_only(type(exc), exc)
        result.crashed = True
        result.error_type = f"build_url:{type(exc).__name__}"
        result.error_message = str(exc)
        result.traceback_line = tb_lines[-1].strip() if tb_lines else repr(exc)

    return result


def _normalize_warning(warning: str) -> str:
    """Сворачивает warning в шаблон класса: конкретные имена/фразы -> плейсхолдер.

    Пример: ``«хата»: не удалось распознать, не попало в ссылку`` и
    ``«школа рядом»: не удалось распознать, не попало в ссылку`` схлопываются
    в один шаблон ``«…»: не удалось распознать, не попало в ссылку`` — это и
    есть «один класс дефекта», а не два разных.
    """
    return _QUOTED_RE.sub("«…»", warning).strip()


def _clean_phrase(phrase: str) -> str:
    """Убирает пунктуацию/пробелы по краям — для сравнения фраз и «сирот»."""
    return phrase.strip(" ,.-:;!?()").lower()


@dataclass
class AuditReport:
    """Агрегированный отчёт по всему корпусу (машинно- и человекочитаемый)."""

    generated_at: str
    corpus_path: str
    total_queries: int
    num_crashes: int
    num_empty_criteria: int
    num_near_empty_criteria: int
    num_no_filter_urls: int
    avg_coverage_ratio: float
    warning_classes: list[dict[str, Any]]
    top_unrecognized_phrases: list[dict[str, Any]]
    orphan_fragments: list[dict[str, Any]]
    empty_criteria_queries: list[str]
    no_filter_url_queries: list[str]
    crashes: list[dict[str, Any]]
    results: list[dict[str, Any]]


def _build_report(corpus_path: Path, results: list[QueryResult]) -> AuditReport:
    """Считает все агрегаты из списка результатов прогона."""
    crashes = [r for r in results if r.crashed]
    ok_results = [r for r in results if not r.crashed]

    # (a) Пустые/почти пустые критерии при непустом (содержательном) тексте.
    empty_criteria = [
        r for r in ok_results if not r.criteria and _significant_word_count(r.text) > 0
    ]
    near_empty_criteria = [
        r
        for r in ok_results
        if len(r.criteria) <= 1 and _significant_word_count(r.text) > _NEAR_EMPTY_WORD_THRESHOLD
    ]

    # (b) URL получился совсем без фильтров (ни пути, ни query).
    no_filter_urls = [r for r in ok_results if r.url == _BASE_URL]

    # (c) Фрагменты-«сироты» длиной 1-2 значимых символа среди нераспознанного.
    orphan_counter: Counter[str] = Counter()
    # (d) Топ повторяющихся нераспознанных фраз (полных, не «сирот»).
    phrase_counter: Counter[str] = Counter()
    # Классы дефектов — по нормализованному шаблону warning'а.
    class_counter: Counter[str] = Counter()
    class_examples: dict[str, list[str]] = {}

    for r in ok_results:
        for warning in r.warnings:
            template = _normalize_warning(warning)
            class_counter[template] += 1
            examples = class_examples.setdefault(template, [])
            if len(examples) < 5 and r.text not in examples:
                examples.append(r.text)

            match = _UNRECOGNIZED_RE.search(warning)
            if match:
                phrase = _clean_phrase(match.group("phrase"))
                if not phrase:
                    continue
                phrase_counter[phrase] += 1
                if len(phrase) <= _ORPHAN_MAX_LEN:
                    orphan_counter[phrase] += 1

    warning_classes = [
        {"template": template, "count": count, "examples": class_examples[template]}
        for template, count in class_counter.most_common()
    ]
    top_phrases = [
        {"phrase": phrase, "count": count}
        for phrase, count in phrase_counter.most_common(_TOP_PHRASES_LIMIT)
    ]
    orphans = [{"phrase": phrase, "count": count} for phrase, count in orphan_counter.most_common()]

    total = len(results)
    avg_coverage = (
        sum(r.coverage_ratio for r in ok_results) / len(ok_results) if ok_results else 0.0
    )

    return AuditReport(
        generated_at=datetime.now(UTC).isoformat(),
        corpus_path=str(corpus_path),
        total_queries=total,
        num_crashes=len(crashes),
        num_empty_criteria=len(empty_criteria),
        num_near_empty_criteria=len(near_empty_criteria),
        num_no_filter_urls=len(no_filter_urls),
        avg_coverage_ratio=avg_coverage,
        warning_classes=warning_classes,
        top_unrecognized_phrases=top_phrases,
        orphan_fragments=orphans,
        empty_criteria_queries=[r.text for r in empty_criteria],
        no_filter_url_queries=[r.text for r in no_filter_urls],
        crashes=[
            {
                "text": r.text,
                "error_type": r.error_type,
                "error_message": r.error_message,
                "traceback_line": r.traceback_line,
            }
            for r in crashes
        ],
        results=[asdict(r) for r in results],
    )


def _print_report(report: AuditReport, *, only_warnings: bool) -> None:
    """Печатает читаемый отчёт в stdout."""
    print("=" * 78)
    print("АУДИТ ПАРСЕРА picurl — офлайн, без сети, без ИИ")
    print("=" * 78)
    print(f"Корпус: {report.corpus_path}")
    print(f"Всего запросов: {report.total_queries}")
    print(f"CRASH: {report.num_crashes}")
    print(f"Пустые критерии (при непустом тексте): {report.num_empty_criteria}")
    print(
        f"Почти пустые критерии (>= {_NEAR_EMPTY_WORD_THRESHOLD} значимых слов, "
        f"<= 1 поле критериев): {report.num_near_empty_criteria}"
    )
    print(f"URL без единого фильтра: {report.num_no_filter_urls}")
    print(f"Средняя доля покрытия текста: {report.avg_coverage_ratio:.1%}")

    print("\n" + "-" * 78)
    print(f"ТОП-{_TOP_CLASSES_LIMIT} КЛАССОВ ДЕФЕКТОВ (по частоте warning-шаблона)")
    print("-" * 78)
    for i, cls in enumerate(report.warning_classes[:_TOP_CLASSES_LIMIT], start=1):
        print(f"{i:2d}. [{cls['count']:3d}x] {cls['template']}")
        for example in cls["examples"][:2]:
            snippet = example if len(example) <= 90 else example[:87] + "..."
            print(f"       напр.: {snippet!r}")

    if report.crashes:
        print("\n" + "-" * 78)
        print(f"CRASH ({len(report.crashes)})")
        print("-" * 78)
        for c in report.crashes:
            print(f" - {c['error_type']}: {c['traceback_line']}")
            print(f"   текст: {c['text']!r}")

    print("\n" + "-" * 78)
    print(f"(a) ПУСТЫЕ КРИТЕРИИ ПРИ НЕПУСТОМ ТЕКСТЕ ({report.num_empty_criteria})")
    print("-" * 78)
    for q in report.empty_criteria_queries[:20]:
        print(f" - {q!r}")
    if len(report.empty_criteria_queries) > 20:
        print(f"   ... и ещё {len(report.empty_criteria_queries) - 20}")

    print("\n" + "-" * 78)
    print(f"(b) URL БЕЗ ЕДИНОГО ФИЛЬТРА ({report.num_no_filter_urls})")
    print("-" * 78)
    for q in report.no_filter_url_queries[:20]:
        print(f" - {q!r}")
    if len(report.no_filter_url_queries) > 20:
        print(f"   ... и ещё {len(report.no_filter_url_queries) - 20}")

    print("\n" + "-" * 78)
    print(f"(c) ФРАГМЕНТЫ-«СИРОТЫ» ДЛИНОЙ 1-2 СИМВОЛА ({len(report.orphan_fragments)})")
    print("-" * 78)
    for orphan in report.orphan_fragments[:30]:
        print(f" - {orphan['phrase']!r} x{orphan['count']}")

    print("\n" + "-" * 78)
    print(f"(d) ТОП-{_TOP_PHRASES_LIMIT} ПОВТОРЯЮЩИХСЯ НЕРАСПОЗНАННЫХ ФРАЗ")
    print("-" * 78)
    for i, p in enumerate(report.top_unrecognized_phrases, start=1):
        print(f"{i:2d}. [{p['count']:3d}x] {p['phrase']!r}")

    if only_warnings:
        print("\n" + "-" * 78)
        print("ЗАПРОСЫ С WARNINGS (--only-warnings)")
        print("-" * 78)
        for r in report.results:
            if r["warnings"] and not r["crashed"]:
                print(f"\n[{r['text']!r}]")
                print(f"  criteria: {r['criteria']}")
                print(f"  url:      {r['url']}")
                for w in r["warnings"]:
                    print(f"  warning:  {w}")


def _write_json(report: AuditReport, path: Path) -> None:
    """Пишет машинный отчёт в JSON (создаёт родительский каталог при нужде)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser_ = argparse.ArgumentParser(
        description=(
            "Офлайн-аудит парсера picurl: прогоняет корпус запросов через "
            "parse() -> build_url() без сети и без ИИ, агрегирует дефекты классами."
        )
    )
    parser_.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS_PATH,
        help=f"Путь к корпусу запросов (по умолчанию {DEFAULT_CORPUS_PATH}).",
    )
    parser_.add_argument("--limit", type=int, default=None, help="Ограничить число запросов.")
    parser_.add_argument(
        "--only-warnings",
        action="store_true",
        help="В консольном отчёте дополнительно вывести детали по каждому запросу с warnings.",
    )
    parser_.add_argument(
        "--json-out",
        type=Path,
        default=DEFAULT_JSON_OUT_PATH,
        help=f"Куда писать машинный JSON-отчёт (по умолчанию {DEFAULT_JSON_OUT_PATH}).",
    )
    parser_.add_argument(
        "--quiet",
        action="store_true",
        help="Не печатать подробный отчёт в stdout (только итоговая строка).",
    )
    return parser_.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    queries = _load_corpus(args.corpus, args.limit)
    results = [_run_one(text) for text in queries]
    report = _build_report(args.corpus, results)

    _write_json(report, args.json_out)

    if not args.quiet:
        _print_report(report, only_warnings=args.only_warnings)

    print(
        f"\nГотово: {report.total_queries} запросов, {report.num_crashes} crash(ей), "
        f"JSON: {args.json_out}"
    )
    return 1 if report.num_crashes else 0


if __name__ == "__main__":
    raise SystemExit(main())
