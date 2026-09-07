"""Graph-driven toolbar sweep (button sweep 2.0).

Walks the toolbar surfaces from the wo-test-harness repo's
harness-graph/graph.json (the projection of features.yaml + the editor
sources, resolved via WO_HARNESS_GRAPH or a sibling checkout) and asserts —
against the
LIVE editor — that:

1. every registered ``toolbar:{cmd}`` surface exists in the editor iframe,
2. every ``[data-cmd]`` actually present in the editor is known to the graph.

Adding a command to editor + features.yaml automatically extends direction 1;
removing the graph entry while the button remains fails direction 2. No
hardcoded button lists.

feature register: F-010 F-011 F-012 F-013 F-014 F-015 F-016 F-017 F-018 F-030 F-031 F-033 F-034 F-035 F-036 F-037 F-038 F-041 F-050 F-051 F-060 F-061 (harness-graph)
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

import pytest

from conftest import (
    BASE,
    dav_mkcol,
    dav_put,
    docx_bytes,
    editor_frame,
    goto,
    open_file_by_name,
)

def _find_graph() -> Path:
    """Locate harness-graph graph.json (register moved to wo-test-harness)."""
    env = os.environ.get("WO_HARNESS_GRAPH")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        raise SystemExit(f"WO_HARNESS_GRAPH={env} is not a file")
    here = Path(__file__).resolve()
    # CI checks wo-test-harness out inside the server workspace; locally it is
    # a sibling of the checkout parent. Accept both, then legacy in-repo.
    candidates = [
        # CI (docserver.yml) checks wo-test-harness out INSIDE the server
        # workspace: $GITHUB_WORKSPACE/wo-test-harness == parents[2]/wo-test-harness.
        here.parents[2] / "wo-test-harness" / "harness-graph" / "graph.json",
        # Local dev checkout: harness is a sibling of the server checkout's
        # grandparent (~/git/wo-test-harness next to ~/git/World-Office/...).
        here.parents[3].parent / "wo-test-harness" / "harness-graph" / "graph.json",
        # Legacy in-repo path (pre-move).
        here.parents[2] / "scripts" / "harness-graph" / "graph.json",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise SystemExit(
        "harness graph not found: set WO_HARNESS_GRAPH to the wo-test-harness "
        "harness-graph/graph.json path"
    )


GRAPH = _find_graph()


def _graph_toolbar_commands() -> set[str]:
    g = json.loads(GRAPH.read_text())
    return {
        n["id"].split(":", 1)[1]
        for n in g["nodes"]
        if n.get("label") == "Surface" and n["id"].startswith("toolbar:")
    }


@pytest.fixture()
def editor_toolbar(page, run_id):
    """One editor session shared by the sweep tests (login is expensive)."""
    folder = f"e2e-sweep-{random.randint(1000, 9999)}"
    for attempt in range(4):
        r = dav_mkcol(f"{run_id}/{folder}")
        if r.status_code in (201, 204, 405):
            break
        time.sleep(1.5 * (attempt + 1))
    else:
        pytest.fail(f"MKCOL {run_id}/{folder} failed: {r.status_code}")
    name = "sweep-anchor.docx"
    for attempt in range(4):
        r = dav_put(f"{run_id}/{folder}/{name}", docx_bytes())
        if r.status_code in (201, 204):
            break
        time.sleep(1.5 * (attempt + 1))
    else:
        pytest.fail(f"PUT {run_id}/{folder}/{name} failed: {r.status_code}")
    goto(page, f"{BASE}/files/spaces/personal/admin/{run_id}/{folder}")
    page.locator("[data-test-resource-name]").first.wait_for(state="visible", timeout=25000)
    open_file_by_name(page, name)
    frame = editor_frame(page)
    frame.locator("[data-cmd]").first.wait_for(state="attached", timeout=20000)
    yield frame


def _dom_commands(frame) -> set[str]:
    loc = frame.locator("[data-cmd]")
    out: set[str] = set()
    for i in range(loc.count()):
        v = loc.nth(i).get_attribute("data-cmd")
        if v:
            out.add(v.strip())
    return out


@pytest.mark.gui
def test_every_registered_surface_exists(editor_toolbar):
    """Direction 1: features.yaml surface -> live editor button."""
    registered = _graph_toolbar_commands()
    assert registered, "graph.json has no toolbar surfaces — regenerate it"
    dom = _dom_commands(editor_toolbar)
    missing = sorted(registered - dom)
    assert not missing, (
        f"registered toolbar surfaces missing from the live editor: {missing}; "
        f"editor has: {sorted(dom)}"
    )


@pytest.mark.gui
def test_every_editor_button_is_registered(editor_toolbar):
    """Direction 2: live editor button -> features.yaml surface."""
    registered = _graph_toolbar_commands()
    dom = _dom_commands(editor_toolbar)
    unknown = sorted(dom - registered)
    assert not unknown, (
        f"editor exposes data-cmd buttons unknown to features.yaml: {unknown}; "
        "add them to the register (stable F-id!) and regenerate graph.json"
    )
