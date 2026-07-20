CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE ai_structured_facts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_type TEXT NOT NULL,        -- 'complex' | 'district' | 'county'
    subject_id TEXT NOT NULL,          -- id из соответствующего справочника
    fact_type TEXT NOT NULL,           -- 'is_center' | 'poi_school' | 'poi_kindergarten' | ...
    fact_value JSONB NOT NULL,         -- {"present": true, "distance_m": 340}
    source TEXT NOT NULL DEFAULT 'ai', -- 'ai' | 'geo' | 'curated' | 'promoted'
    confidence REAL NOT NULL DEFAULT 1.0,
    observed_count INT NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (subject_type, subject_id, fact_type)
);

CREATE TABLE ai_semantic_cache (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    query_signature TEXT NOT NULL,      -- нормализованный нераспознанный остаток текста
    embedding halfvec(384) NOT NULL,    -- размерность = выбранная embedding-модель, см. п.3
    raw_question TEXT NOT NULL,
    answer JSONB NOT NULL,
    hit_count INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON ai_semantic_cache USING hnsw (embedding halfvec_cosine_ops);
