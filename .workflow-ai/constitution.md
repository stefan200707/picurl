Have enough. Composing the final constitution document now.

# picurl — Project Constitution for AI Coding Agents

Source: distilled from `CLAUDE.md`, `pyproject.toml`, `prompts/_conventions.md`, `app/`, `tests/`. When in doubt, `CLAUDE.md` wins.

## Stack facts
Python 3.12+, `uv` package manager, FastAPI ≥0.115 (entrypoint `app.main:app`), pydantic v2, httpx, rapidfuzz, asyncpg/psycopg+pgvector, ruff, pytest+pytest-asyncio (`asyncio_mode=auto`).

## Code style (verified, consistent across files)
- Type hints everywhere; pydantic v2 models with `model_config = ConfigDict(extra="forbid", ...)` for strict contracts (`Criteria`, `MatchedEntity`, `LandmarkRequirement`, etc).
- Line length 100, `target-version=py312` (ruff-enforced). Ruff select: `E,W,F,I,UP,B,SIM,C4,RUF`.
- Cyrillic strings/docstrings/comments are intentional — `RUF001/002/003` disabled. **Comments and docstrings are Russian**, prose explains *why*, often citing an invariant number, an `AUDIT_REPORT.md`/`docs/` section, or a defect. Preserve this practice for new code.
- Module docstrings at top of file explain the module's role in the pipeline and cross-reference `Criteria`/other modules (see `app/parsing/schema.py`, `app/pik/url_builder.py`).
- Enums: `StrEnum`/`IntEnum` with `.slug`/`.id`/`.field`/`.order` properties for URL mapping, not ad-hoc dicts scattered in call sites.
- Functions: FastAPI style `Annotated[X, Depends(...)]`; return-type annotations preferred over `response_model=`.
- Scalar optional fields default `None`; list fields default `Field(default_factory=list)` — never `None` for lists (documented decision in `schema.py`).
- `field_validator` used to dedupe lists while preserving order (`list(dict.fromkeys(value))`).
- Inline numbered comments (`# 1. ...`, `# 2. ...`) used in `url_builder`/`schema.to_query_dict` to mirror spec ordering — keep numbering stable if extending.
- Logging: `logging.getLogger(__name__)`, `logger.error(..., exc_info=True)` on caught exceptions before converting to HTTPException/warning.
- Run `ruff check` and `ruff format` before finishing any change.

## Never break (invariants, from `CLAUDE.md` §Инварианты)
1. **Nothing silently dropped.** Any unparsed/unsupported fragment → `warnings` or `option_candidates`.
2. **Single-path / multi-query rule**: exactly one value → URL path slug; ≥2 → query param with comma-joined ids/GUIDs. Truth source: `docs/pik-url-schema.md`.
3. **LLM never computes distances or invents filters.** Geo-narrowing is pure haversine; any AI answer must pass `sanitize_against_shortlist`/`sanitize_option_resolution`/`sanitize_landmark_resolution`; invented data is discarded, phrase stays in `warnings`.
4. **Merge never overwrites curated `name`/`slug`/`id`/`aliases`.** Stable sort required.
5. **Never merge a record that has non-empty `id`/`slug` into another** — loses its GUID; `cleanup()` must raise `ValueError` on such collision.
6. **Runtime reads reference JSON from disk only.** Network calls limited to `refresh*` scripts and the validator's single request per build-url call.
7. **Missing URL form (`slug`/`id`) → warning, never silence.**
8. `agy` CLI invocations: always `--print`, **never** `--dangerously-skip-permissions` (prompt-injection surface).
9. **No AI credentials = AI layer off, not an error.** Base pipeline must work without it.
10. **Promotion of AI facts requires N independent observations** (one confident hallucination must not promote). Alias promotion only on unanimous phrase→slug agreement.
11. Latin→Cyrillic homoglyph normalization is 1:1 (string length unchanged).
12. `fully_resolved` stays conservative — any unknown fact routes to AI, never guessed.
13. Truncation to a limit happens **after** distance sorting, everywhere in ranking code.
14. Fallback distances are real haversine values, never template text.
15. `aclosing` must wrap SDK streaming generators (otherwise GC on live loop leaks the CLI process).
16. Station-class fallback applies only at default radius; explicit `max_distance_m` is a hard cutoff.
17. `Criteria` model uses `extra="forbid"` — do not add fields elsewhere; edit `app/parsing/schema.py` and keep `to_public_dict`/`to_query_dict` in sync.
18. Geo-fallback intersects (`AND`), never unions, with already-selected ЖК; `build_url` and `validate` **must both** go through `app.pik.location_fallback.combine_with_fallback` (past divergence inflated `result_count` 6.5x).
19. `TaggedWarning` (subclass of `str`) must remain interchangeable with plain `str` for `==`, `in`, `remove()`, regex, `deepcopy`, `json`, pydantic. New strings (concat/slice/f-string) do not inherit category — don't try to make them.
20. `warnings_detailed` is built **before** constructing `BuildUrlResponse` (pydantic coerces `TaggedWarning` subclass to plain `str` inside models); flat `warnings` is derived from it, single source of truth.

## Public API / data contracts
- `app/parsing/schema.py::Criteria` — central contract; `extra="forbid"`, empty `Criteria()` must stay valid. `rooms`/`finish` always lists. `sort` values: `price_asc|price_desc|area_asc|area_desc`.
- `app/reference/*.json` — `RefEntry` format `{name, slug, id, aliases[]}`, `id` as string. Both `slug` and `id` needed (path vs query use).
- `refresh` overwrites only `complexes/counties/metro/districts`; `/internal/refresh-dicts` requires `X-Internal-Token` == `INTERNAL_REFRESH_TOKEN`.
- `app/config.py::Settings` is the single config source (env/.env via pydantic-settings) — don't read env vars ad hoc elsewhere.
- `POST /build-url` response shape: `{url, criteria, result_count, warnings, warnings_detailed, ai_used, ai_failed, ai_cache_hit, ai_explanation, map_config}` — treat as stable API, extend additively only.
- `docs/pik-url-schema.md` is the sole source of truth for the pik.ru URL filter schema — don't invent params.

## Testing conventions
- `tests/` mirrors `app/` structure (`parsing/`, `reference/`, `pik/`, `api/`, `ai/`, `geo/`, `integration/`).
- Every non-trivial module gets unit 
... [truncated]
