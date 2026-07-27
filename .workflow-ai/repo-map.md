I have enough verified detail. Writing the map.

# Repository Map — picurl

FastAPI microservice: free-form Russian apartment queries → pik.ru URL with applied filters. Deterministic parsing (regex + rapidfuzz over local JSON dicts); optional AI enrichment (Anthropic/Antigravity) with Postgres+pgvector memory. Python 3.12+, managed by `uv`.

## Entry Points
- `app/main.py` — FastAPI app (`app.main:app`), `lifespan` inits httpx client + optional asyncpg pool; `GET /health`; includes API router.
- `app/api/endpoints.py` — routes: `POST /build-url` (parse→enrich→build→validate), `POST /internal/refresh-dicts` (token-gated dict refresh + cache clear).
- Run: `uv run fastapi dev` or `uv run uvicorn app.main:app --reload` (:8000, Swagger `/docs`).
- CLI: `uv run python -m app.reference.refresh` (refresh dicts); `app/geo/refresh_poi.py`, `app/ai/promotion.py` runnable modules.

## Config
- `pyproject.toml` — deps, `[tool.fastapi] entrypoint`, ruff (line 100, py312), pytest (`testpaths=tests`, `asyncio_mode=auto`, `pythonpath=.`).
- `app/config.py` — `Settings` (pydantic-settings, `.env`): `INTERNAL_REFRESH_TOKEN`, `AI_ENRICHMENT_ENABLED`, `AI_PROVIDER`, `ANTHROPIC_API_KEY`, `ANTIGRAVITY_CLI_PATH/MODEL`, `AI_MODEL_NAME`, `DATABASE_URL`, `AI_PROMOTION_MIN_OBSERVATIONS/CONFIDENCE`. `get_settings()` cached.
- `docker-compose.yml`, `SETUP.md`, `ENABLE_AI.md`, `ENABLE_ANTIGRAVITY.md`, `ANTIGRAVITY_INSTRUCTIONS.md`, `docs/db-setup.md` — DB/AI setup.

## Modules

### app/parsing/ — fact extraction (deterministic)
- `parser.py` — facade `parse(text) -> ParseResult(criteria, warnings)`; merges spans, applies rules + entity match.
- `rules/` — regex rule modules: `core.py`, `area.py`, `finish.py`, `floor.py`, `price.py`, `rooms.py`, `time.py`, `poi.py`, `misc.py`; `apply_rules`, `Span` in `__init__.py`.
- `entity_match.py` — rapidfuzz fuzzy match to reference entities; `match_entities`, cached `build_choices`.
- `schema.py` — `Criteria` model (internal contract) + `to_public_dict()`.
- `stopwords.py` — `STOP_WORDS`.

### app/pik/ — URL generation
- `url_builder.py` — `build_url(criteria)` → pik.ru search URL.
- `validator.py` — async `validate(criteria, client)` → result_count/warning via pik backend.

### app/reference/ — local JSON dictionaries + refresh
- `loader.py` — load dicts, `clear_cache`.
- `refresh.py` — `run_refresh()` fetches fresh dicts from pik backend API (`python -m`).
- JSON data: `metro.json`, `counties.json`, `districts.json`, `complexes.json`, `benefits.json`, `options.json`, `option_groups.json`, `poi_cache.json`.

### app/ai/ — AI enrichment + self-learning memory (optional)
- `enrichment.py` — `enrich(text, criteria, warnings, pool)`, `merge_enrichment`, `AIMeta`, `EnrichmentResult`; orchestrates model call + semantic cache + geo candidates.
- `client.py` — `call_model` (Claude/Antigravity provider abstraction).
- `embeddings.py` — `embed` (sentence-transformers).
- `memory.py` — asyncpg store: `StructuredFact`, `CachedAnswer`, `lookup/store_structured_fact`, `lookup/store_semantic`; `DATABASE_URL` env default `postgresql://…/picurl_ai`.
- `promotion.py` — promote AI facts → deterministic dicts (thresholds from config).
- `prompts.py` — `SYSTEM_PROMPT`, `build_context`. `schema.py` — `AIEnrichmentAnswer`, `ComplexCandidate`.

### app/geo/ — geospatial POI/candidate resolution
- `poi.py` — Overpass API POI queries; `POICategory`, `POIResult`, `get_overpass_query`.
- `distance.py` — distance calc. `candidates.py` — `build_candidate_shortlist`, `build_query_signature`, `resolve_known_facts`.
- `refresh_poi.py` — refresh POI cache.

### app/knowledge_base/
- `README.md` only (no code).

## Tests — `tests/` (pytest, asyncio auto; mirrors app/)
- `conftest.py`, `test_health.py`.
- `parsing/` — test_parser, test_rules, test_schema, test_entity_match(+_conj,_mock).
- `pik/` — test_url_builder, test_validator. `reference/` — test_loader, test_refresh.
- `api/test_endpoints.py`. `ai/` — test_enrichment, test_memory, test_promotion. `geo/` — test_candidates, test_distance, test_poi.
- `integration/` — test_build_url_e2e, test_ai_learning_loop.

## Docs & prompts
- `docs/` — `pik-url-schema.md`, `ai-enrichment-architecture.md`, `db-setup.md`.
- `prompts/` — 22 numbered build-decomposition prompts (`00`–`21`) + `_conventions.md`, `_open-questions.md`, README.
- Root reports: `README.md` (RU), `audit.md`, `report.md`, `center_report.md`, `explanation.md`, `stress_test_queries.md`, `CLAUDE.md`.

## Pruned / ignored
- `.git/`, `tasks/67c6aa33…/` (nested duplicate git checkout — ignore), `.claude/skills/ — ~80 files of agent skill docs (not app code)`, `.workflow-ai/verify.sh`.

Note: dev tooling — `uv sync`, `uv run pytest`, `uv run ruff check|format`.
