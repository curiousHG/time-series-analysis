"""BackgroundRefreshGroup.poll(compact=True) summarises running tasks in one caption line."""

from __future__ import annotations

from ui.components import background_refresh as br


def test_compact_summary_lists_running_tasks_with_progress(monkeypatch):
    metas = {"a": {"phase": "engine", "done": 3, "total": 10}, "b": {}}
    monkeypatch.setattr(br, "task_state", lambda key: type("S", (), {"meta": metas[key]})())
    text = br.compact_summary([("a", "Run A"), ("b", "Run B")])
    assert text == "2 running · Run A [3/10] · Run B"


def test_compact_summary_singular():
    text = br.compact_summary([("a", "Run A")])
    assert text.startswith("1 running · Run A")


def test_poll_compact_renders_single_caption(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(br, "is_running", lambda key: key == "a")
    monkeypatch.setattr(br, "task_state", lambda key: type("S", (), {"meta": {"done": 1, "total": 4}})())
    monkeypatch.setattr(br.st, "fragment", lambda **kw: (lambda fn: fn))
    monkeypatch.setattr(br.st, "caption", lambda text: captured.setdefault("captions", []).append(text))
    monkeypatch.setattr(br.st, "info", lambda *a, **k: captured.setdefault("info", []).append(a))
    monkeypatch.setattr(br.st, "progress", lambda *a, **k: captured.setdefault("progress", []).append(a))
    group = br.BackgroundRefreshGroup((br.BackgroundRefresh("a", "Run A"), br.BackgroundRefresh("b", "Run B")))
    group.poll(compact=True)
    assert captured["captions"] == ["1 running · Run A [1/4]"]
    assert "info" not in captured and "progress" not in captured
