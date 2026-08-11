I have everything needed. Writing the map.

# Repository Map: picurl

Free-form Russian apartment wishes → pik.ru filtered search URL. FastAPI service. Deterministic regex+fuzzy parsing; optional LLM enrichment with self-learning Postgres/pgvector memory. Python 3.12, uv, ruff, pytest-asyncio.

## Pruned tree
```
app/
  main.py                 # FastAPI app, lifespan, /health
  config.py               # pydantic-settings (env/.env)
  api/                    # HTTP contract + endpoints
  parsing/                # deterministic fact extraction
    rules/                # per-facet regex extractors
  reference/              # local JSON dicts + refresh + *.json (8 files)
  pik/                    # URL builder + result validator
  ai/                     # LLM enrichment + memory/promotion
    migrations/01_memory_tables.sql
  geo/                    # POI/geo shortlist + distance
  knowledge_base/README.md  # docs only (no code)
docs/ — 3 files (architecture, db-setup, pik-url-schema)
prompts/ — 25 build-decomposition .md files
tests/                    # mirrors app/
docker-compose.yml        # postgres pgvector/pg15 (picurl_ai)
pyproject.toml            # deps, ruff, pytest config
README.md SETUP.md ENABLE_AI.md ENABLE_ANTIGRAVITY.md ANTIGRAVITY_INSTRUCTIONS.md
audit.md report.md center_report.md explanation.md stress_test_queries.md stress_test_output.txt
CLAUDE.md .claude/  .workflow-ai/verify.sh
[ignored] tasks/<uuid>/  — full duplicate checkout incl .venv & extra 02_ai_call_log.sql; not the live tree
```

## Entry points
- **HTTP app**: `app/main.py:app` (`uv run fastapi dev` / `uvicorn app.main:app`). Endpoints in `app/api/endpoints.py`: `POST /build-url`, `POST /internal/refresh-dicts` (token via `X-Internal-Token`); `GET /health` in main.
- **CLI/module runners**: `python -m app.reference.refresh` (`refresh.py:main`); `app/ai/promotion.py:main` (promote AI facts); `app/geo/refresh_poi.py:main` (refresh POI cache).

## Configuration
- `app/config.py` — `Settings`/`get_settings()`: `AI_ENRICHMENT_ENABLED`, `AI_PROVIDER` (claude|antigravity), `ANTHROPIC_API_KEY`, `AI_MODEL_NAME`, `ANTIGRAVITY_*`, `DATABASE_URL`, `INTERNAL_REFRESH_TOKEN`, `AI_PROMOTION_MIN_OBSERVATIONS/CONFIDENCE`. Reads `.env`.
- `pyproject.toml` — deps + `[tool.fastapi] entrypoint="app.main:app"`, ruff (line 100, py312), pytest (`testpaths=["tests"]`, `asyncio_mode=auto`, `pythonpath=["."]`).
- `docker-compose.yml` — pgvector postgres for AI memory. `app/ai/migrations/01_memory_tables.sql` schema.

## Modules

### app/api — HTTP contract
- `schemas.py` — `BuildUrlRequest`, `BuildUrlResponse` (url, criteria, result_count, warnings, ai_* fields).
- `endpoints.py` — orchestrates pipeline: parse → AI enrich/merge → build_url → validate; refresh-dicts endpoint.

### app/parsing — deterministic extraction (regex + fuzzy)
- `parser.py:parse(text)->ParseResult` — facade; merges spans, flags unparsed significant chunks as warnings.
- `schema.py` — internal `Criteria` model (+`to_public_dict()`), enums `Rooms`/`Finish`/`Sort`/`HousingType`, `POIRequirement`, `MatchedEntity`.
- `entity_match.py` — fuzzy match text→reference entities (rapidfuzz); `match_entities`, `build_choices` (cached), windowing/scoring.
- `stopwords.py` — stopword lists.
- `rules/__init__.py:apply_rules` — runs all extractors → `RulesOutcome`.
- `rules/` extractors: `price.py:extract_price`, `area.py:extract_area`, `floor.py:extract_floor`, `rooms.py:extract_rooms`, `finish.py:extract_finish/extract_ready`, `time.py:extract_time_to_metro`, `poi.py:extract_poi_requirements`, `misc.py` (settlement_year, sort, required_tags, housing_type, only_available, unsupported, fallback_metro), `core.py` (normalize/span helpers).

### app/reference — local dictionaries
- `loader.py` — cached loaders (`load_metro/counties/districts/complexes/benefits/option_groups/options`, `load_all`), `RefEntry`/`ReferenceData`, `normalize`, `find_by_name`, `clear_cache`.
- `refresh.py` — fetch pik.ru backend blocks, derive/merge/write JSON dicts (`refresh`/`run_refresh`/`main`).
- JSON data: `metro.json counties.json districts.json complexes.json benefits.json option_groups.json options.json poi_cache.json`.

### app/pik — URL + validation
- `url_builder.py:build_url(criteria)->str` — compose pik.ru search URL from Criteria.
- `validator.py:validate(criteria, client)->ValidationResult` — query pik.ru for result_count/warnings (async httpx).

### app/ai — LLM enrichment + self-learning memory
- `enrichment.py` — `enrich()` main entry, `merge_enrichment`, `sanitize_against_shortlist`, `persist`; `AIMeta`, `EnrichmentResult`.
- `client.py` — provider dispatch `call_model`; `call_claude` (Anthropic), `call_antigravity`; transient-retry.
- `memory.py` — asyncpg store/lookup of `StructuredFact` & semantic `CachedAnswer`; `DATABASE_URL`.
- `embeddings.py` — sentence-transformers `embed(text)` (cached model).
- `promotion.py` — promote high-confidence repeated AI facts into deterministic dicts (`find_promotable_facts`, `promote`, `run_promotion`, `main`).
- `prompts.py:build_context` — assemble system/user prompt; `schema.py` — `AIEnrichmentAnswer`, `ComplexCandidate`.
- `migrations/01_memory_tables.sql`.

### app/geo — geo/POI reasoning
- `candidates.py` — `build_candidate_shortlist`, `resolve_known_facts`, `fully_resolved`, `build_query_signature` (feeds AI shortlist).
- `poi.py` — Overpass API POI fetch; `POICategory`, `POIResult`, `fetch_poi`.
- `distance.py` — `haversine`, `is_center`.
- `refresh_poi.py` — batch refresh POI cache.

## Tests (`tests/`, mirrors app/)
`tests/test_health.py`, `conftest.py`; `parsing/` (test_parser, test_rules, test_schema, test_entity_match[_conj/_mock]); `reference/` (test_loader, test_refresh); `pik/` (test_url_builder, test_validator); `api/test_endpoints.py`; `ai/` (test_enrichment, test_memory, test_promotion); `geo/` (test_candidates, test_distance, test_poi); `integration/` (test_build_url_e2e, test_ai_learning_loop). Run: `uv run pytest`.

## Docs
`docs/pik-url-schema.md` (URL format), `docs/ai-enrichment-architecture.md`, `docs/db-setup.md`. `SETUP.md`/`ENABLE_AI.md`/`ENABLE_ANTIGRAVITY.md` = operational setup. `prompts/` = original task decomposition (reference only).
