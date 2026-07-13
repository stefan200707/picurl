#!/usr/bin/env bash
set -euo pipefail

# Автопроверка picurl: выведена из pyproject.toml (CI-конфига в репо нет).
# Стек — Python 3.12, uv, ruff, pytest (+pytest-asyncio, asyncio_mode=auto).
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
