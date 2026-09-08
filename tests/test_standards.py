"""Mechanical enforcement of docs/standards.md — the codebase's engineering standards.

Every rule is either ZERO-TOLERANCE (clean today, locked forever) or a RATCHET: current
violations are frozen in an allowlist; adding a new one fails, and fixing an old one also
fails until the allowlist entry is removed — so the lists only ever shrink.

Scans the app packages once with ast; runs in well under a second.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PACKAGES = ("ui", "services", "data", "core", "stocks", "mutual_funds", "indicators", "strategies")
DOMAIN_PACKAGES = ("stocks", "mutual_funds", "indicators", "strategies", "core")
_SKIP_PARTS = {"__pycache__"}


def _files() -> dict[str, ast.Module]:
    out: dict[str, ast.Module] = {}
    for pkg in APP_PACKAGES:
        for path in sorted((ROOT / pkg).rglob("*.py")):
            if _SKIP_PARTS.intersection(path.parts):
                continue
            rel = str(path.relative_to(ROOT))
            out[rel] = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    return out


FILES = _files()


def _imports(tree: ast.Module) -> list[tuple[str, list[str]]]:
    """[(module_path, [imported names])] for absolute imports anywhere in the file (incl. deferred)."""
    found: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, []) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, [alias.name for alias in node.names]))
    return found


def _ratchet(rule: str, violations: set[str], allowlist: set[str]) -> None:
    new = violations - allowlist
    fixed = allowlist - violations
    assert not new, f"{rule}: NEW violations (fix them or justify in docs/standards.md):\n  " + "\n  ".join(sorted(new))
    assert not fixed, (
        f"{rule}: these allowlist entries no longer violate — remove them from the allowlist "
        f"in tests/test_standards.py to lock the improvement in:\n  " + "\n  ".join(sorted(fixed))
    )


# ---------------------------------------------------------------- layering


def test_streamlit_only_in_ui():
    """services/data/domain stay Streamlit-free so they're callable from CLI/bot/tests."""
    bad = {
        rel
        for rel, tree in FILES.items()
        if not rel.startswith("ui/") and any(mod.split(".")[0] == "streamlit" for mod, _ in _imports(tree))
    }
    assert not bad, f"streamlit imported outside ui/: {sorted(bad)}"


def test_no_reverse_layering():
    """Nothing below the UI layer imports ui.*."""
    bad = {
        rel
        for rel, tree in FILES.items()
        if not rel.startswith("ui/") and any(mod.split(".")[0] == "ui" for mod, _ in _imports(tree))
    }
    assert not bad, f"reverse (→ ui) imports: {sorted(bad)}"


def test_domain_packages_stay_leaf():
    """stocks/mutual_funds/indicators/strategies/core import neither services nor data.*."""
    violations = {
        rel
        for rel, tree in FILES.items()
        if rel.split("/")[0] in DOMAIN_PACKAGES
        and any(mod.split(".")[0] in {"services", "data"} for mod, _ in _imports(tree))
    }
    _ratchet(
        "domain→services/data",
        violations,
        allowlist={"mutual_funds/holdings_stats.py"},  # imports _resolve_slug — Wave 0 fix
    )


def test_no_private_imports_across_packages():
    """`_name` symbols are module-internal; other top-level packages must not import them."""
    violations = set()
    for rel, tree in FILES.items():
        pkg = rel.split("/")[0]
        for mod, names in _imports(tree):
            src = mod.split(".")[0]
            if src in APP_PACKAGES and src != pkg and any(n.startswith("_") for n in names):
                violations.add(f"{rel} ← {mod}")
    _ratchet(
        "cross-package private imports",
        violations,
        allowlist={
            "mutual_funds/holdings_stats.py ← data.repositories.holdings",  # _resolve_slug
            "services/data_freshness.py ← data.repositories.holdings",  # _slug_to_code_map_cached
        },
    )


def test_views_import_data_via_seam():
    """Views reach data through ui/state/loaders.py or a service — not data.* directly. Frozen at
    today's 15 files; route each through the seam and delete its entry."""
    violations = {
        rel
        for rel, tree in FILES.items()
        if rel.startswith("ui/")
        and rel != "ui/state/loaders.py"  # the sanctioned seam
        and any(mod.split(".")[0] == "data" for mod, _ in _imports(tree))
    }
    _ratchet(
        "ui→data bypasses the loaders/services seam",
        violations,
        allowlist={
            "ui/views/mf_screener/page.py",
            "ui/views/mutual_fund/holdings_tab.py",
            "ui/views/mutual_fund/page.py",
            "ui/views/mutual_fund/selector.py",
            "ui/views/overview/indexes.py",
            "ui/views/overview/page.py",
            "ui/views/portfolio/growth.py",
            "ui/views/portfolio/page.py",
            "ui/views/settings/amfi.py",
            "ui/views/settings/page.py",
            "ui/views/settings/refresh.py",
            "ui/views/settings/tradebook.py",
            "ui/views/stock_analysis/fundamentals.py",
            "ui/views/stock_analysis/page.py",
            "ui/views/stock_screener/page.py",
        },
    )


# ---------------------------------------------------------------- data writes


def test_upserts_only_in_repositories():
    """pg_insert / ON CONFLICT belongs to data/repositories — nowhere else builds SQL writes.
    Two services currently do (frozen below); the Wave-2 upsert-helper consolidation moves them."""
    violations = {
        rel
        for rel, tree in FILES.items()
        if not rel.startswith("data/repositories/")
        and any(mod == "sqlalchemy.dialects.postgresql" and "insert" in names for mod, names in _imports(tree))
    }
    _ratchet(
        "postgres upsert built outside data/repositories/",
        violations,
        allowlist={"services/registry_service.py", "services/stock_metrics.py"},
    )


# ---------------------------------------------------------------- caching


def _cache_decorators() -> list[tuple[str, str, bool]]:
    """(file, 'cache_data'|'cache_resource', has_ttl) for every st.cache_* decorator."""
    out = []
    for rel, tree in FILES.items():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute) and target.attr in ("cache_data", "cache_resource"):
                    has_ttl = isinstance(dec, ast.Call) and any(k.arg == "ttl" for k in dec.keywords)
                    out.append((rel, target.attr, has_ttl))
    return out


def test_ui_caching_goes_through_loaders():
    """st.cache_data lives in ui/state/loaders.py (one cache surface). Page-local caches are frozen
    at today's set; new ones belong in loaders.py."""
    violations = {
        rel for rel, kind, _ in _cache_decorators() if kind == "cache_data" and rel != "ui/state/loaders.py"
    }
    _ratchet(
        "cache_data outside ui/state/loaders.py",
        violations,
        allowlist={
            "ui/components/freshness_banner.py",
            "ui/views/mutual_fund/selector.py",
            "ui/views/settings/refresh.py",
            "ui/views/settings/stocks.py",
        },
    )


def test_cache_data_requires_ttl():
    """A cache without ttl serves stale data until a manual clear — every cache_data declares ttl.
    The 4 no-ttl loaders are frozen; Wave 0 gives them ttls and empties this list."""
    violations = {f"{rel}::{kind}" for rel, kind, has_ttl in _cache_decorators() if kind == "cache_data" and not has_ttl}
    _ratchet(
        "cache_data without ttl",
        violations,
        allowlist={"ui/state/loaders.py::cache_data"},  # 4 sites in this file — see docs/standards.md §4
    )


# ---------------------------------------------------------------- errors & logging


def test_no_suppress_exception_in_views():
    """User-facing operations surface failures (core.notifications.push_notice) — they are never
    silently suppressed in a view."""
    violations = set()
    for rel, tree in FILES.items():
        if not rel.startswith("ui/"):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "suppress"
                and any(isinstance(a, ast.Name) and a.id == "Exception" for a in node.args)
            ):
                violations.add(rel)
    _ratchet(
        "contextlib.suppress(Exception) in ui/",
        violations,
        allowlist={"ui/views/stock_analysis/fundamentals.py", "ui/views/stock_analysis/page.py"},
    )


def test_single_risk_free_rate():
    """Exactly one risk-free rate feeds Sharpe/Sortino: services/constants.py. A second definition
    made the same fund show different Sharpe per view."""
    violations = set()
    for rel, tree in FILES.items():
        if rel == "services/constants.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id in ("RISK_FREE", "RF_DAILY", "RISK_FREE_ANNUAL"):
                        violations.add(rel)
    _ratchet("risk-free rate defined outside services/constants.py", violations, allowlist={"ui/constants.py"})


def test_loggers_use_module_name():
    """logging.getLogger(__name__), not a hardcoded dotted path (stale on rename). Namespace
    loggers ('perf', 'boot', 'data') are fine — only dotted module-path literals are banned."""
    violations = set()
    for rel, tree in FILES.items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getLogger"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "." in node.args[0].value
            ):
                violations.add(rel)
    _ratchet(
        "getLogger with hardcoded dotted path",
        violations,
        allowlist={
            "data/fetchers/mutual_fund.py",
            "data/fetchers/screener_in.py",
            "data/fetchers/stock.py",
            "data/repositories/holdings.py",
            "data/repositories/metadata.py",
            "data/repositories/nav.py",
            "data/repositories/scheme_codes.py",
            "data/repositories/scheme_metrics.py",
        },
    )
