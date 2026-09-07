# ONLYOFFICE conformance study — what we checked out, learned, and built

> Date: 2026-08-26  ·  Scope: the four editorial features the docserver gained
> in the parity rounds — **bookmarks, comments, tracked changes,
> cross-references** — in both DOCX and ODF 1.2.

## What we checked out

The ONLYOFFICE **native `core`** repository was sparse-cloned
(`git clone --filter=blob:none --sparse`, ~11 MB) and the format writers were
studied rather than their test documents being copied:

| Source | What it is |
|---|---|
| `OOXML/DocxFormat/Logic/Run.cpp` | DOCX run/paragraph writer (bookmark + comment markers) |
| `OOXML/DocxFormat/Logic/Hyperlink.cpp` | `w:hyperlink` (external `r:id` vs internal `w:anchor`) |
| `OOXML/DocxFormat/Comments.cpp` | `word/comments.xml` reader/writer |
| `OdfFile/Writer/Format/paragraph_elements.cpp` | `text:bookmark*`, `text:bookmark-ref` elements |
| `OdfFile/Writer/Format/text_elements.cpp` | `text:tracked-changes` / `text:changed-region` / change marks |
| `OdfFile/Writer/Format/odf_text_context.cpp` | field/ref emission (`text:bookmark-ref` + `text:reference-format`) |
| `OdfFile/Writer/Format/office_annotation.cpp` | ODT comments |

(ONLYOFFICE's own public test *documents* are not vendored in a single repo —
their QA framework gem has only generic samples. The `core` writer sources are
the authoritative statement of the canonical structures.)

## Canonical structures ONLYOFFICE writes (verified from source)

### DOCX
- **Bookmark** — `w:bookmarkStart{w:id,w:name}` … runs … `w:bookmarkEnd{w:id}`.
- **Comment** — `w:commentRangeStart{w:id}` runs `w:commentRangeEnd` +
  `w:commentReference`, body in `word/comments.xml` (`w:comment{w:id,w:author,w:date}`).
- **Tracked change** — `w:ins{w:id,w:author,w:date}` → `w:r/w:t`;
  `w:del{w:id,w:author,w:date}` → `w:r/w:delText`.
- **Cross-reference** — `w:hyperlink{w:anchor=NAME}` (no `r:id`, no external
  relationship) with a display-text run.

### ODF 1.2
- **Bookmark (range)** — `text:bookmark-start{text:name}` … `text:bookmark-end{text:name}`;
  point form = bare `text:bookmark{text:name}`.
- **Cross-reference** — `text:bookmark-ref{text:ref-name, text:reference-format="text"}`
  with the display text as children. ONLYOFFICE `fieldRef` always carries
  `text:reference-format` (default `text`).
- **Tracked change** — body marks `text:change-start{text:change-id}` …
  `text:change-end{text:change-id}` plus a document-level `text:tracked-changes`
  registry: `text:changed-region{xml:id}` → `text:insertion|text:deletion`
  → `office:change-info` → `dc:creator` (+`dc:date`) and a `text:p` body.
- **Comment** — `office:annotation` with `dc:creator`, `dc:date` and a `text:p`
  body, placed immediately after the anchored runs (it anchors the runs that
  precede it inside the paragraph).

## What this confirmed about the docserver converter

The parity-round implementation already matched every one of these structures:

- `text:bookmark-start`/`text:bookmark-end` range form (odfpy rejects bare
  `text:bookmark` with children, so the range form is the only correct choice).
- DOCX cross-ref via `w:hyperlink`/`w:anchor` with no external rel.
- `w:ins`/`w:del` carry unique `w:id` + `w:author` + `w:date` (`_next_track_id`).
- ODT tracked changes written in the `text:tracked-changes` + `xml:id`
  `changed-region` model — the same model LibreOffice itself writes.
- ODT comments as `office:annotation` + dc children.

## Improvements made from the study

1. **ODT cross-reference writer** — `text:bookmark-ref` now also emits
   `text:reference-format="text"` (matches ONLYOFFICE/LibreOffice output).
   Previously only `text:ref-name` was written; readers tolerate the omission,
   but emitting it is more canonical.
2. **New conformance suite** — `tests/test_onlyoffice_conformance.py`
   (24 tests — expanded) rebuilds each canonical document
   rebuilds each canonical document *in memory* (via python-docx/odfpy) exactly
   as ONLYOFFICE writes it, and asserts the docserver converter:
   - reads canonical DOCX bookmarks, anchor cross-refs, `w:ins`/`w:del` (+delText);
   - reads canonical ODT bookmarks, `bookmark-ref` cross-refs, tracked changes
     (xml:id registry + change marks), `office:annotation` comments;
   - writes canonical `w:bookmarkStart`/`BookmarkEnd`, `w:id`+`w:author`+`w:date`
     on `w:ins`/`w:del`, `text:bookmark-start`/`end` ranges,
     `text:bookmark-ref` with `text:reference-format`.

These are **behavioral conformance tests derived from the source**, not copied
fixture files — they compile even without the ONLYOFFICE checkout present.

## How to repeat the study

```sh
git clone --depth 1 --filter=blob:none --sparse https://github.com/ONLYOFFICE/core.git /tmp/oo-core
cd /tmp/oo-core
git sparse-checkout set OOXML/DocxFormat OdfFile/Writer/Format
```

---

## State-of-the-art test methods now layered on

Beyond the hand-written conformance cases the converter is also tested with
modern/automated methods (see module docstrings for details):

* **Differential testing** — `tests/test_converter_differential.py` uses
  LibreOffice headless (soffice) as an independent ground-truth engine:
  our DOCX reader must agree textually with LibreOffice on real documents,
  and every DOCX/ODT our writers emit must open in LibreOffice with the
  authored content intact. Skips cleanly when soffice is absent. *(On this
  host LibreOffice's TXT export of tables runs away — a LO limitation
  documented in the module — so tables stay covered by round-trip tests.)*
* **Property-based + fuzz + metamorphic + mutation** — `tests/
  test_converter_property.py` (Hypothesis): random documents preserve every
  source token through DOCX/ODT round-trips; our DOCX output is a semantic
  fixed point (idempotence); composed HTML never crashes either converter;
  DOCX and ODT round-trips of the same source converge on identical text;
  plus a mutation smoke test proving the invariants have teeth.

Environment notes for the LibreOffice oracle on headless hosts:
`SAL_USE_VCLPLUGIN=svp`, isolated module-shared profile, `--norestore
--nodefault --nolockcheck`, small batches (<10 files), and verify output
files exist (LO can exit 0 without producing output).

## Relation to the wo-conformance borrow

- The earlier borrow (`tests/test_conformance_corpus.py`, 30 cases) feeds the
  converter the real `.docx` files from `conformance/corpus/cases/` (wo-test-harness repo)
  — genuine documents, but they only cover basic formatting (fonts, spacing,
  tables, page breaks) and predate the bookmarks/comments/tracked-changes/
  cross-reference work.
- This suite closes that gap with ONLYOFFICE-informed, feature-focused
  conformance coverage for exactly the four editorial features.
