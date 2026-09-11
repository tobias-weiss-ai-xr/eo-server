"""Browser-suite lane for parallel runs.

Every test here spins its own uvicorn server + Chromium + WOPI httpd.
Running a dozen of those concurrently starves the CPU and flakes waits,
so when the browser lane runs INSIDE the full suite, ``--dist loadgroup``
pins its files to a small set of workers via ``xdist_group`` buckets
(``WO_BROWSER_WORKERS``, default 2) while the pure unit tests spread
across the remaining cores. For a dedicated browser run, set
``WO_BROWSER_WORKERS=0`` to add NO grouping so the lane spreads freely
across all workers (fastest; ``flaky(reruns=2)`` absorbs transient render
waits).

Browser tests additionally carry ``flaky(reruns=2)``: under a saturated
machine a single slow fetch can leave the page permanently un-rendered,
and a retry distinguishes that from a real regression (which fails
twice). Every retry is visible in the report as a RERUN entry.

Set ``WO_BROWSER_LANE=0`` to drop the browser lane entirely.
No-op when xdist is not installed (plain serial runs).
"""

from __future__ import annotations

import gc
import hashlib
import os
import re
import time
import urllib.request
from pathlib import Path

import pytest


_FAIL_DIR = Path("/tmp/e2e-failures")


def _dump_failure(item) -> None:
    """Save server + browser state on e2e failure so the next iteration
    diagnoses from evidence instead of blind guessing. A failed test burns
    its full _wait timeout window (x flaky reruns) before surfacing a bare
    AssertionError — these artifacts turn that dead time into a lead.
    Best-effort: must never mask or alter the test outcome."""
    servers = item.funcargs.get("servers")
    if not servers:
        return
    try:
        _FAIL_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", item.name)[:80]
        base = str(_FAIL_DIR / f"{int(time.time())}-{safe}")
        errs = []
        for url, path in (
            (f"http://127.0.0.1:{servers['doc_port']}/editor/{servers['doc_id']}",
             base + ".editor.html"),
            (f"http://127.0.0.1:{servers['host_port']}/wopi/files/{servers['doc_id']}"
             f"/contents?access_token={servers['token']}",
             base + ".saved.docx"),
        ):
            try:
                Path(path).write_bytes(urllib.request.urlopen(url, timeout=5).read())
            except Exception as exc:
                errs.append(f"{url}: {exc}")
        try:
            from playwright.sync_api import Page

            for obj in gc.get_objects():
                if isinstance(obj, Page) and not obj.is_closed():
                    errs.append(f"live page: {obj.url}")
                    try:
                        Path(base + ".page.html").write_text(obj.content())
                        obj.screenshot(path=base + ".page.png")
                    except Exception as exc:
                        errs.append(f"page dump: {exc}")
        except ImportError:
            pass
        Path(base + ".log").write_text(
            f"test: {item.nodeid}\ntime: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            + "\n".join(errs) + "\n"
        )
    except Exception:
        pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    rep = yield
    if hasattr(rep, "get_result"):  # pluggy >= 1.3 wraps in Result
        rep = rep.get_result()
    if rep.when == "call" and rep.failed:
        _dump_failure(item)


def pytest_collection_modifyitems(config, items) -> None:
    if os.environ.get("WO_BROWSER_LANE") == "0":
        return
    workers = int(os.environ.get("WO_BROWSER_WORKERS", "2"))
    for item in items:
        if "/e2e/" in str(item.fspath):
            item.add_marker(pytest.mark.flaky(reruns=2))
            # failure artifacts: see _dump_failure below
            try:
                import xdist  # noqa: F401
            except ImportError:
                continue
            if workers <= 0:
                continue  # free-for-all lane (dedicated browser run)
            digest = hashlib.md5(str(item.fspath).encode()).hexdigest()
            bucket = int(digest, 16) % workers
            item.add_marker(pytest.mark.xdist_group(f"browser-{bucket}"))
