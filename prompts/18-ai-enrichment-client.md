# 18 — Клиент Claude и пайплайн обогащения — Milestone AI-3

> Прочитай `_conventions.md`, `docs/ai-enrichment-architecture.md` (промпт 15)
> и project skill `claude-api` перед началом — свериться с ним по актуальному
> списку моделей/параметрам Messages API на момент реализации, не полагаться
> на память/устаревшие названия моделей.

## Контекст

Это ядро ИИ-слоя: функция, которую вызывает эндпоинт (промпт 19), когда
детерминированный гео-слой (16) и карта памяти (17) не дали полного ответа.
Формулировка задачи прямо требует, чтобы **запрос в ИИ шёл после анализа
бэком** — то есть модель не получает сырой текст пользователя как есть, а
получает уже отфильтрованный бэкендом контекст: короткий шорт-лист кандидатов
ЖК (не весь справочник — это и дорого, и увеличивает риск галлюцинации) и уже
известные структурные факты.

## Задача

### 1. `app/ai/schema.py` — контракт запроса/ответа

```python
class ComplexCandidate(BaseModel):
    id: str
    name: str
    district: str | None
    county: str | None
    metro: list[str]
    is_center: bool | None          # None = неизвестно (гео-слой не смог определить)
    known_poi: dict[str, bool]      # {"school": True, "kindergarten": None, ...} — None = неизвестно

class AIEnrichmentAnswer(BaseModel):
    matched_complex_ids: list[str]      # подмножество из переданного шорт-листа, не шире
    center_district_ids: list[str] = [] # если пользователь спрашивал про "центр"
    poi_findings: dict[str, dict[str, bool]] = {}  # {complex_id: {category: present}}
    explanation: str
    confidence: float
```

`matched_complex_ids`/`center_district_ids`/ключи `poi_findings` **обязаны**
быть подмножеством того, что было передано модели в запросе — это
проверяется кодом после ответа (см. п.4), а не доверяется модели на слово.

### 2. `app/ai/client.py` — обёртка над Anthropic SDK

- `async def call_model(system_prompt: str, user_payload: dict) ->
  AIEnrichmentAnswer` — Messages API с принудительной структурой ответа
  (tool use / response schema — выбрать актуальный механизм по skill
  `claude-api`), таймаут (например 15с), одна попытка ретрая при сетевой
  ошибке/таймауте, без ретрая при ошибке валидации схемы (это осмысленная
  ошибка модели/промпта, а не транзиентный сбой).
- Модель и ключ — из `app/config.py` (промпт 15), не хардкодить.
- Если `AI_ENRICHMENT_ENABLED=False` или `ANTHROPIC_API_KEY` не задан —
  `call_model` не должен вызываться вообще (проверка на уровне оркестрации,
  п.4), а не тихо возвращать заглушку.

### 3. `app/ai/prompts.py` — системный промпт и сборка контекста

Системный промпт формулирует задачу модели явно ограниченно: «Тебе даны
кандидаты ЖК с известными фактами (район/округ/метро/признак центра/POI).
Реши, какие из НИХ подходят под запрос пользователя. Никогда не упоминай ЖК,
которых нет в списке. Если факт неизвестен (`null`) — не утверждай его
наличие/отсутствие, оставь соответствующий ключ вне `poi_findings` или явно
отметь как неизвестный, в зависимости от финальной схемы ответа». Включить
few-shot пример на кейсе из задачи («нужна квартира трёшка в центре рядом
продуктовые и детсады и школы»). Функция сборки контекста
`build_context(text, criteria, candidates, known_facts) -> dict` — берёт
шорт-лист кандидатов из гео-слоя (промпт 19 передаёт), а не весь
`complexes.json`.

### 4. `app/ai/enrichment.py` — оркестрация (сердце промпта 15)

```python
async def enrich(text: str, criteria: Criteria, warnings: list[str]) -> EnrichmentResult:
    if not criteria.poi_requirements and not criteria.center_requested:
        return EnrichmentResult.noop()  # ключевой guard: без семантического остатка ИИ не трогаем

    candidates = build_candidate_shortlist(criteria)          # гео-слой, промпт 16
    known = resolve_known_facts(candidates, criteria)          # промоутнутые факты + гео, без БД/ИИ

    if fully_resolved(known, criteria):
        return EnrichmentResult.from_deterministic(known)      # ai_used=False

    signature = build_query_signature(text, criteria)          # нормализованный остаток
    cached = await memory.lookup_semantic(signature, embed(signature))
    if cached is not None:
        return EnrichmentResult.from_cache(cached)              # ai_used=False, cache_hit=True

    if not settings.AI_ENRICHMENT_ENABLED:
        warnings.append("ИИ-обогащение выключено — часть запроса не обработана")
        return EnrichmentResult.disabled()

    try:
        answer = await client.call_model(system_prompt, build_context(...))
    except Exception:
        logger.error(..., exc_info=True)
        warnings.append("не удалось обработать ИИ-обогащение (ошибка сервиса)")
        return EnrichmentResult.failed()

    answer = sanitize_against_shortlist(answer, candidates)     # защита от галлюцинаций
    await persist(answer, candidates)                            # запись в карту памяти (17)
    return EnrichmentResult.from_ai(answer)
```

(Псевдокод иллюстративный — реализовать с реальными типами/обработкой ошибок
по стилю остального проекта, `try/except` с точечными исключениями, не голый
`except Exception` без причины, если можно сузить.)

- `sanitize_against_shortlist` — обязательная функция: выкидывает из ответа
  всё, чего не было среди `candidates`; если после очистки список пуст —
  трактовать как «не найдено», не как ошибку.
- `persist` пишет и в `ai_structured_facts` (по каждому подтверждённому
  факту из `poi_findings`/`center_district_ids`), и в `ai_semantic_cache`
  (весь ответ целиком, для повторных похожих формулировок).

## Требования к результату

- Тесты `tests/ai/test_enrichment.py` с **полностью замоканным** Anthropic
  клиентом (никаких реальных вызовов API в тестах/CI — тот же принцип, что и
  для `validator`/`refresh`): проверить (а) guard — без `poi_requirements`
  ИИ не вызывается вовсе; (б) sanitize отбрасывает ЖК не из шорт-листа; (в)
  таймаут/ошибка сети → `warnings` пополняется, пайплайн не падает; (г)
  `AI_ENRICHMENT_ENABLED=False` → warning, ИИ не дергается.
- `uv run pytest`, `uv run ruff check` зелёные.

## Обнови CLAUDE.md и `docs/ai-enrichment-architecture.md`

Финальная версия системного промпта (или ссылка на файл), схема
`AIEnrichmentAnswer`, политика санитайза и бюджет (1 вызов на запрос, 1
ретрай, таймаут).

## Зависит от

15, 16, 17.
