"""The Settings → Reference diagram is generated; keep it structurally sound and in sync with
data.sources.SOURCE_ROUTING."""

from data.sources import SOURCE_ROUTING
from ui.views.settings import data_sources as ref


def test_diagram_structure_is_valid():
    assert ref.validate_diagram() == []


def test_every_node_has_a_tooltip_and_appears_in_the_diagram():
    code = ref.build_flow_diagram()
    tips = ref.tooltips()
    for node_id in ref.node_ids():
        assert node_id in tips
        assert f"{node_id}[" in code or f"{node_id}(" in code


def test_every_external_routing_row_is_explained_by_exactly_one_source_tooltip():
    tips = ref.tooltips()
    for row in SOURCE_ROUTING:
        if not ref.is_external_row(row):
            continue
        owners = [n_id for n_id in tips if row["what"] in tips[n_id] and n_id in {s["id"] for s in ref._SOURCES}]
        assert len(owners) >= 1, row["what"]


def test_fallback_source_is_labelled_in_its_tooltip():
    assert "fallback for" in ref.tooltips()["KUV"]
