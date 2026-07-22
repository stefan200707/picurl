from pydantic import BaseModel


class ComplexCandidate(BaseModel):
    id: str
    name: str
    district: str | None
    county: str | None
    metro: list[str]
    is_center: bool | None
    known_poi: dict[str, bool | None]
    # Координаты ЖК — объективный факт из справочника (complexes.json). Нужны
    # детерминированному слою (build_candidate_shortlist) для сужения/сортировки
    # кандидатов по дистанции до ориентира. Модель их не «прикидывает» — расчёт
    # расстояния делает app/geo/distance.haversine, а не LLM.
    lat: float | None = None
    lon: float | None = None


class AIEnrichmentAnswer(BaseModel):
    matched_complex_ids: list[str]
    center_district_ids: list[str] = []
    poi_findings: dict[str, dict[str, bool]] = {}
    explanation: str
    confidence: float


class OptionMatch(BaseModel):
    """Сопоставление нераспознанной фразы с slug'ом опции/группы опций.

    ``slug`` — ``None``, если модель не нашла соответствия (фраза остаётся в
    ``warnings`` как есть, ничего не выдумываем). Значение slug обязательно
    валидируется против реального справочника
    (:func:`app.ai.enrichment.sanitize_option_resolution`) — как для кандидатов
    ЖК, строке из ответа модели не доверяем слепо.
    """

    phrase: str
    slug: str | None = None
    confidence: float = 0.8


class OptionResolutionAnswer(BaseModel):
    """Ответ модели на резолвинг фраз-синонимов фильтров под опции."""

    matches: list[OptionMatch] = []
