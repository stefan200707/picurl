"""Категория и severity предупреждения — носитель поверх ``str`` (задача Г6, шаг 1).

Повод и обоснование формы — ``docs/warnings-severity-proposal.md``. Коротко:
``warnings`` в этом проекте **не выходной буфер, а внутренний канал данных**. Это
один мутируемый ``list[str]``, который передаётся по ссылке; из него
``app.ai.enrichment`` удаляет элементы **по точному равенству строк**, а
``_residual_fragments`` разбирает строки регексом, чтобы вернуть фрагменты в ИИ.
На суффикс «не удалось распознать, не попало в ссылку» опирается ещё и
``scripts/parse_audit.py``, а на нём — пороги QA.

Поэтому носитель категории — **подкласс ``str``**: он несёт атрибут
``category``, но для ``==``, ``in``, ``list.remove()``, регекса, pydantic и JSON
остаётся обычной строкой. Ни одно из ~62 мест формирования текста, ни механика
снятия устаревших warning'ов, ни аудит-скрипты, ни пороги не правятся.

ОГРАНИЧЕНИЕ, которое надо помнить: любая операция, порождающая НОВУЮ строку
(конкатенация, срез, ``.strip()``, ``.replace()``, f-строка), возвращает обычный
``str`` и категорию **теряет**. На канале warnings таких операций сегодня нет —
элементы переносятся целиком; правило зафиксировано здесь, чтобы не завелись.
"""

from collections.abc import Iterable
from enum import StrEnum


class WarningCategory(StrEnum):
    """Смысл предупреждения. Граница между категориями проведена там, где у
    пользователя **разное действие** по ту сторону (таблица §1 предложения)."""

    #: Требование распознано, но в ссылку не доехало. Ссылка неполная.
    LOST = "lost"
    #: Фрагмент не распознан, природа неизвестна. Честный третий исход.
    UNKNOWN = "unknown"
    #: Корректно отброшено, фильтром не было. Действий не требует.
    NOISE = "noise"
    #: Такого фильтра у pik.ru нет — менять ожидания, а не запрос.
    CAPPED = "capped"
    #: Фильтр применён, но ``result_count`` его не учитывает: верить ссылке, не числу.
    UNVERIFIED = "unverified"
    #: Не применили по своей вине (ИИ/данные/сеть) — единственный случай, когда
    #: осмысленна кнопка «повторить запрос».
    DEGRADED = "degraded"
    #: Не проблема вовсе, справка о том, как сузили выдачу.
    INFO = "info"


class WarningSeverity(StrEnum):
    """Уровень для фильтрации/сортировки на клиенте."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


#: Severity — **производная** от категории, а не независимое поле: клиенту хватит
#: severity для фильтрации, категория нужна для формулировки.
_SEVERITY_BY_CATEGORY: dict[WarningCategory, WarningSeverity] = {
    WarningCategory.LOST: WarningSeverity.ERROR,
    WarningCategory.UNKNOWN: WarningSeverity.ERROR,
    WarningCategory.DEGRADED: WarningSeverity.WARNING,
    WarningCategory.CAPPED: WarningSeverity.WARNING,
    WarningCategory.UNVERIFIED: WarningSeverity.INFO,
    WarningCategory.NOISE: WarningSeverity.INFO,
    WarningCategory.INFO: WarningSeverity.INFO,
}


def severity_of(category: WarningCategory) -> WarningSeverity:
    """Severity по категории. Неизвестная категория — ``error``: непонятое
    предупреждение не должно тонуть под справками."""
    return _SEVERITY_BY_CATEGORY.get(WarningCategory(category), WarningSeverity.ERROR)


class TaggedWarning(str):
    """Строка предупреждения, несущая ``category``.

    Создаётся ровно там, где формируется текст. Всё остальное обращается с ней
    как с обычной строкой — см. ограничение в докстринге модуля.
    """

    # __slots__ здесь невозможен: str — тип переменной длины, непустой __slots__
    # у его подкласса запрещён интерпретатором.

    category: WarningCategory

    def __new__(cls, text: str, category: WarningCategory = WarningCategory.UNKNOWN):
        obj = super().__new__(cls, text)
        obj.category = WarningCategory(category)
        return obj

    @property
    def severity(self) -> WarningSeverity:
        return severity_of(self.category)

    def __repr__(self) -> str:
        return f"TaggedWarning({str.__repr__(self)}, {self.category.value!r})"


def category_of(item: str) -> WarningCategory:
    """Категория элемента канала warnings.

    Неразмеченная строка (а сегодня это большинство из ~62 точек) — ``unknown``.
    Это **нормальный промежуточный статус**, а не дефект: порции разметки
    внедряются по одной, остаток всё это время живёт как ``unknown`` без вреда.
    """
    category = getattr(item, "category", None)
    return WarningCategory(category) if category is not None else WarningCategory.UNKNOWN


def describe(item: str) -> tuple[str, WarningCategory, WarningSeverity]:
    """``(текст, категория, severity)`` для одного элемента канала."""
    category = category_of(item)
    return str(item), category, severity_of(category)


def iter_described(
    items: Iterable[str],
) -> Iterable[tuple[str, WarningCategory, WarningSeverity]]:
    """То же для всего списка — единственный источник правды для API-ответа."""
    return (describe(item) for item in items)
