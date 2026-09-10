"""E2E — ribbon geometry gate (fast drift catcher, no screenshots).

The parity chain has three cheap layers:
  1. OO goldens pin the tab label centers (r16, test_file_menu.py pins the
     --qa-x contract values against measured OO positions);
  2. THIS gate asserts the LIVE layout honors those contracts:
       - every visible .ribbon-tab's rendered center == its --qa-x (±2px),
       - visible tabs never overlap,
       - on EVERY tab, every ribbon button has a non-zero rect fully inside
         the strip (catches clipped / collapsed / blank-geometry buttons,
         e.g. the emoji-glyph class of bug, on all tabs — not just the ones
         a screenshot round happens to capture).
  3. the visual walker samples real ink only at round end.

Runs in ~seconds: one browser session, DOM-rect reads only.

Marker: RIBBON-GEOMETRY: OK
"""

from __future__ import annotations

import pytest

from tests.e2e.test_cloud_editor_e2e import _parent_url, _seed_doc
from tests.e2e.test_cloud_editor_e2e import servers as _servers


@pytest.fixture(scope="module")
def servers(_servers):
    return _servers


TOL = 2.0  # px — rendered center vs declared --qa-x


def test_ribbon_geometry_matches_declared_contract(servers):
    """Layout-level geometry gate. Emits RIBBON-GEOMETRY: OK when every tab
    center, spacing and per-tab button rect honors the declared contract."""
    from playwright.sync_api import sync_playwright

    seed = _seed_doc(servers, "geometry.docx")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            parent = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
            parent.goto(_parent_url(servers, seed))
            frame = parent.frame("ed")
            frame.locator("#editor").wait_for(state="visible", timeout=45000)

            report = frame.evaluate(
                """(tol) => {
                const out = { tabs: [], badTabs: [], pages: {} };
                const strip = document.querySelector('#ribbon') ||
                              document.querySelector('.ribbon-page')?.parentElement;
                const stripRect = strip ? strip.getBoundingClientRect() : null;

                // 1) visible tab centers == --qa-x; 2) no pairwise overlap
                const tabs = [...document.querySelectorAll('.ribbon-tab')]
                  .filter((t) => !t.hidden && t.offsetParent !== null);
                const rects = tabs.map((t) => t.getBoundingClientRect());
                tabs.forEach((t, i) => {
                  const declared = parseFloat(getComputedStyle(t).getPropertyValue('--qa-x'));
                  const center = rects[i].left + rects[i].width / 2;
                  out.tabs.push({ tab: t.dataset.tab, declared, center });
                  if (Number.isFinite(declared) && Math.abs(center - declared) > tol)
                    out.badTabs.push(`${t.dataset.tab}: center ${center.toFixed(1)} vs --qa-x ${declared}`);
                });
                for (let i = 0; i < rects.length; i++)
                  for (let j = i + 1; j < rects.length; j++)
                    if (rects[i].left < rects[j].right - 0.5 && rects[j].left < rects[i].right - 0.5)
                      out.badTabs.push(`overlap: ${tabs[i].dataset.tab} ↔ ${tabs[j].dataset.tab}`);
                if (stripRect && rects.length) {
                  const last = rects[rects.length - 1];
                  if (last.right > stripRect.right + 0.5)
                    out.badTabs.push(`tab strip overflow: last tab right ${last.right.toFixed(1)} > strip ${stripRect.right.toFixed(1)}`);
                }

                // 3) per tab: activate the page, then every button on it
                //    must have a non-zero rect fully inside the strip
                //    (inactive pages are display:none and legitimately 0x0,
                //    so each page is measured while it is the active one)
                for (const tab of tabs) {
                  tab.click();
                  const page = document.querySelector(`.ribbon-page[data-tab="${tab.dataset.tab}"]`);
                  if (!page) { out.pages[tab.dataset.tab] = ['no .ribbon-page for tab']; continue; }
                  const issues = [];
                  for (const btn of page.querySelectorAll('button')) {
                    if (btn.offsetParent === null) continue; // hidden sub-palette (opened on demand)
                    const r = btn.getBoundingClientRect();
                    const label = btn.id || btn.dataset.stub || (btn.textContent || '').trim().slice(0, 24);
                    if (r.width < 4 || r.height < 4)
                      issues.push(`${label}: degenerate rect ${r.width.toFixed(0)}x${r.height.toFixed(0)}`);
                    if (stripRect && (r.left < stripRect.left - 0.5 || r.right > stripRect.right + 0.5))
                      issues.push(`${label}: clipped horizontally (x ${r.left.toFixed(0)}..${r.right.toFixed(0)})`);
                  }
                  if (issues.length) out.pages[tab.dataset.tab] = issues;
                }
                document.querySelector('.ribbon-tab[data-tab="home"]')?.click();
                return out;
              }""",
                TOL,
            )

            assert not report["badTabs"], f"tab geometry drift: {report['badTabs']}"
            assert not report["pages"], f"button geometry violations: {report['pages']}"
            visible = [t for t in report["tabs"]]
            assert len(visible) >= 10, f"expected the 10-tab strip, saw {len(visible)}"
            print(f"\nRIBBON-GEOMETRY: OK ({len(visible)} tabs, {TOL}px tolerance)")
        finally:
            browser.close()
