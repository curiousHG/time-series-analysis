# Agent workflow (Cursor and other coding agents)

This file orients **automated and human-assisted edits** so they stay consistent with the codebase. Deep architecture, commands, and tables live in **CLAUDE.md**.

## Read first

1. **CLAUDE.md** — commands (`uv`, `pytest`, DB), page map, layer stack, PostgreSQL models, data sources, and graphify rules when `graphify-out/` exists.
2. **`.cursor/rules/*.mdc`** — always-on and scoped rules Cursor loads for this repo.

## Where to implement changes

| Goal | Primary locations |
|------|-------------------|
| New Streamlit page or tab | `ui/app.py`, `ui/views/`, `ui/views/*_tabs/` |
| Reusable widget / chart | `ui/components/`, `ui/charts/` |
| Cached data for UI | `ui/state/loaders.py` |
| Business logic (no Streamlit) | `services/` |
| MF-specific analytics | `mutual_funds/` |
| DB models / engine | `core/models/`, `core/database.py` |
| New or changed external data flow | `data/repositories/` + `data/fetchers/` |
| Indicators / strategies | `core/indicators/`, `core/strategies/` |

## Definition of done

- Run **`uv run ruff check . --exclude notebooks/`** and **`uv run pytest`** unless the task is documentation-only; report or fix failures.
- Respect layer boundaries (see `.cursor/rules/trading-platform-core.mdc`).
- For data integrations, follow the **DB-first `ensure_*`** policy described in CLAUDE.md.

## Optional: repository graph

If **`graphify-out/GRAPH_REPORT.md`** (or `graphify-out/wiki/index.md`) is present, use it for broad “how does X connect to Y” questions before spelunking the whole tree. After substantive code changes, maintainers can run `graphify update .` as noted in CLAUDE.md.
