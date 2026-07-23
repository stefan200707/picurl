# Настройка подключения к базе данных

Единый источник правды по настройке PostgreSQL + `pgvector` (Карта памяти,
семантический кэш, миграции) — **[ENABLE_ANTIGRAVITY.md](../ENABLE_ANTIGRAVITY.md)**.
Общий чек-лист развёртывания — [SETUP.md](../SETUP.md).

Реальные имена таблиц миграции (`app/ai/migrations/01_memory_tables.sql`) —
`ai_structured_facts` и `ai_semantic_cache`.

> Этот документ сведён к ссылке в рамках чистки дублирующейся документации
> (AUDIT_REPORT.md, раздел 4.1), чтобы не было расходящихся копий инструкций.
