"""Differential testing of the docserver converters against LibreOffice.

LibreOffice (headless, ``soffice``) is used as an independent ground-truth
engine:

* **read direction** — documents LibreOffice reads are also read by our
  DOCX→HTML reader (text-level agreement, space-tolerant because the
  LibreOffice TXT filter concatenates runs without their spaces);
* **write direction** — DOCX/ODT documents our writers emit open in
  LibreOffice with the authored content intact (conversion succeed + key
  tokens present is the pass criteria).

Reliability notes learned on this host:

* run headless with an isolated, module-shared profile (``-env:
  UserInstallation``) + ``SAL_USE_VCLPLUGIN=svp`` (virtual display) +
  ``--norestore --nodefault --nolockcheck``;
* keep conversions to small batches — LibreOffice becomes unreliable on
  large single invocations;
* LibreOffice's TXT export of TABLE documents runs away in this environment
  (writes multi-GB temp files) even for real-world corpus tables, so tables
  are not oracle-validated here (they are covered by round-trip conformance).

Skips cleanly when ``soffice`` is not installed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from src.editor.converter import docx_to_html, html_to_docx
from src.editor.odt_converter import html_to_odt

SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(SOFFICE is None, reason="LibreOffice (soffice) not installed"),
]

def _find_corpus_dir() -> Path | None:
    """Locate the wo-conformance corpus; caller skips when None.

    Order: WO_CONFORMANCE_CORPUS env -> sibling wo-test-harness checkout ->
    legacy in-repo path.
    """
    env = os.environ.get("WO_CONFORMANCE_CORPUS")
    if env and Path(env).is_dir():
        return Path(env)
    here = Path(__file__).resolve()
    # CI checks wo-test-harness out inside the server workspace; locally it is
    # a sibling of the checkout parent. Accept both, then legacy in-repo.
    candidates = [
        # CI (docserver.yml) checks wo-test-harness out INSIDE the server
        # workspace: $GITHUB_WORKSPACE/wo-test-harness == parents[2]/wo-test-harness.
        here.parents[2] / "wo-test-harness" / "conformance" / "corpus" / "cases",
        # Local dev checkout: harness is a sibling of the server checkout's
        # grandparent (~/git/wo-test-harness next to ~/git/World-Office/...).
        here.parents[2].parent.parent / "wo-test-harness" / "conformance" / "corpus" / "cases",
        # Legacy in-repo path (pre-move).
        here.parents[2] / "core" / "crates" / "wo-conformance" / "corpus" / "cases",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


CORPUS_DIR = _find_corpus_dir()
# Subset of the wo-conformance corpus that converts reliably under headless
# LibreOffice here; covers plain, bold, bold-italic, heading, mixed-fonts,
# single word. (Table cases are excluded — see module docstring.)
READ_SUBSET = ["01", "03", "05", "11", "22", "25"]


def _plain(html: str) -> str:
    """Rough plain text of generated HTML (tags stripped, entities unescaped)."""
    text = re.sub(r"<[^>]+>", " ", html)
    for ent, ch in (
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "),
    ):
        text = text.replace(ent, ch)
    return text


def _norm(text: str) -> str:
    """Lowercase text with all non-word characters stripped.

    Deliberately space-INSENSITIVE: LibreOffice's TXT filter concatenates
    runs (their spaces vanish), so exact tokens would falsely mismatch
    mixed-format documents.
    """
    text = text.replace("\ufeff", " ").replace("\xa0", " ")
    return re.sub(r"[^\w]+", "", text.lower())


def _lo_env() -> dict:
    env = dict(os.environ)
    env["SAL_USE_VCLPLUGIN"] = "svp"
    return env


def _read_txt(path: Path) -> str:
    return path.read_text(errors="replace") if path.exists() else ""


@pytest.fixture(scope="module")
def lo_shared(tmp_path_factory) -> tuple[Path, dict]:
    """Isolated, warmed LibreOffice profile shared across the module.

    The first headless start on a fresh profile bootstraps LO; warm it up
    once with a trivial conversion so later tests ride on the warm profile.
    """
    td = tmp_path_factory.mktemp("lo")
    prof = td / "prof"
    probe = td / "probe.docx"
    probe.write_bytes(html_to_docx("probe text for profile warm-up"))
    subprocess.run(
        [
            SOFFICE,
            f"-env:UserInstallation=file://{prof}",
            "--headless", "--norestore", "--nodefault", "--nolockcheck",
            "--convert-to", "txt",
            "--outdir", str(td / "out"),
            str(probe),
        ],
        env=_lo_env(), capture_output=True, timeout=300,
    )
    return td, _lo_env()


def _lo_convert(files: list[Path], prof: Path, outdir: Path, env: dict,
                timeout: int = 240) -> None:
    """Convert every file to TXT in one invocation; assert outputs appeared.

    LibreOffice can exit 0 while failing to produce a file, so each expected
    output must exist afterwards (default filter name ``<stem>.txt``).
    """
    files = [f.resolve() for f in files]
    outdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            SOFFICE,
            f"-env:UserInstallation=file://{prof}",
            "--headless", "--norestore", "--nodefault", "--nolockcheck",
            "--convert-to", "txt", "--outdir", str(outdir),
            *(str(f) for f in files),
        ],
        env=env, check=True, capture_output=True, timeout=timeout,
    )
    missing = [f.stem for f in files if not (outdir / f"{f.stem}.txt").exists()]
    assert not missing, f"LibreOffice produced no output for: {missing}"


# ---------------------------------------------------------------------------
# Read direction: our DOCX reader vs LibreOffice on real documents
# ---------------------------------------------------------------------------

def test_docx_reader_text_agrees_with_libreoffice_on_corpus(lo_shared):
    if CORPUS_DIR is None:
        pytest.skip("wo-conformance corpus not checked out")
    td, env = lo_shared
    prof = td / "prof"
    files: list[Path] = []
    for prefix in READ_SUBSET:
        matches = sorted(CORPUS_DIR.glob(f"{prefix}-*.docx"))
        if matches:
            files.append(matches[0])
    if not files:
        pytest.skip("wo-conformance corpus not checked out")

    out = td / "out-read"
    _lo_convert(files, prof, out, env)
    for f in files:
        lo_text = _read_txt(out / f"{f.stem}.txt")
        ours = _norm(_plain(docx_to_html(f.read_bytes())))
        if not lo_text or not ours:
            continue
        ratio = SequenceMatcher(None, _norm(lo_text), ours).ratio()
        assert ratio >= 0.8, (
            f"{f.name}: LibreOffice vs our reader similarity {ratio:.2f}\n"
            f"  LO : {_norm(lo_text)[:120]!r}\n  ours: {ours[:120]!r}"
        )


# ---------------------------------------------------------------------------
# Write direction: docs our writers emit must open in LibreOffice intact
# ---------------------------------------------------------------------------

CONTRACTS: dict[str, str] = {
    "plain": "<p>alpha beta gamma delta epsilon</p>",
    "emphasis": "<p>zeta <b>eta</b> <i>theta</i> iota kappa</p>",
    "bookmark-xref": (
        '<p>lambda <span class="bookmark" data-name="B1">mu</span> nu '
        'see <a href="#B1">xi</a> omicron</p>'
    ),
    "tracked": (
        '<p>pi <ins class="track-insert" data-author="Alice">rho</ins> '
        '<del class="track-delete" data-author="Bob">sigma</del> tau</p>'
    ),
    "comments": (
        '<p><span class="comment" data-author="Carol" '
        'data-comment="a note">upsilon</span> phi</p>'
    ),
    "heading-list": "<h1>alpha one</h1><ul><li>first</li><li>second</li></ul>",
}

# Words that must survive into LibreOffice's TXT export of our written file.
# Comment BODIES are intentionally absent — the TXT filter drops notes.
CONTRACT_WORDS: dict[str, list[str]] = {
    "plain": ["alpha", "delta", "epsilon"],
    "emphasis": ["zeta", "eta", "theta", "iota"],
    "bookmark-xref": ["lambda", "mu", "nu", "xi"],
    "tracked": ["pi", "rho", "sigma", "tau"],  # LibreOffice keeps change content
    "comments": ["upsilon", "phi"],
    "heading-list": ["alpha", "first", "second"],
}


@pytest.mark.parametrize("fmt", ["docx", "odt"])
def test_written_documents_open_in_libreoffice_with_text_intact(fmt: str, lo_shared):
    td, env = lo_shared
    prof = td / "prof"
    writer = html_to_docx if fmt == "docx" else html_to_odt
    base = td / f"write-{fmt}"
    base.mkdir()
    srcs: list[Path] = []
    for name, html in CONTRACTS.items():
        p = base / f"{name}.{fmt}"
        p.write_bytes(writer(html))
        srcs.append(p)
    out = base / "out"
    _lo_convert(srcs, prof, out, env, timeout=300)
    for name, words in CONTRACT_WORDS.items():
        lo_text = _norm(_read_txt(out / f"{name}.txt"))
        missing = [w for w in words if w not in lo_text]
        assert not missing, (
            f"{fmt}/{name}: LibreOffice lost {missing} (LO text: "
            f"{_read_txt(out / f'{name}.txt')[:200]!r})"
        )
