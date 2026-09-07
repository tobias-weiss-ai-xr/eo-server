#!/usr/bin/env python3
"""
generate-tasks.py — Parse the Engine Rebuild Master Plan and emit tasks.json.

Reads:  ../../plan/2026-07-25-engine-rebuild-execution-plan.md  (workspace root)
Writes: ../config/tasks.json

The plan contains pipe-delimited task tables of the form:
    | <ID> | <Task> | <Requires> | <Acceptance> |
for engines FC (foundation, in §2 prose), DM, TL, FL, SS, SL, CH, RT, PDF, CO, SP.

This script extracts those rows, expands dependency shorthand (e.g. "DM-2..8",
"DM-2, SS-1"), attaches an exact file scope per task, and emits a structured
manifest. Re-run after editing the plan to regenerate the manifest; manual edits
to tasks.json will be overwritten.

Usage:
    python3 generate-tasks.py [--plan PATH] [--out PATH] [--check]
        --check   validate only (exit 1 if any task lacks a scope entry)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# scripts/wo-orchestrator/scripts/ -> workspace root
WORKSPACE_ROOT = HERE.parents[3]
DEFAULT_PLAN = WORKSPACE_ROOT / "plan" / "2026-07-25-engine-rebuild-execution-plan.md"
DEFAULT_OUT = HERE.parent / "config" / "tasks.json"

TASK_ID_RE = re.compile(r"^\|\s*((?:FC|DM|TL|FL|SS|SL|CH|RT|PDF|CO|SP)-\d+)\s*\|")
DEP_RANGE_RE = re.compile(r"^([A-Z]+-\d+)\.\.(\d+)$")

# ---------------------------------------------------------------------------
# Foundation contracts (FC-1..4) are defined in §2 prose, NOT in task tables.
# Inject them explicitly so the parser doesn't miss them.
FOUNDATION_TASKS: list[dict] = [
    {"id": "FC-1", "title": "Path + Range addressing types in wo-common",
     "requires_raw": "—", "acceptance_prose": "cargo test -p wo-common path:: — 4 tests: serde round-trip, equality, Range doc, debug format."},
    {"id": "FC-2", "title": "ModelOp enum + EditableModel trait in wo-common",
     "requires_raw": "FC-1", "acceptance_prose": "trait compiles; 3 unit tests on Vec<String> stub proving Insert/Delete/Replace round-trip and invert reverses."},
    {"id": "FC-3", "title": "Uniform WASM export convention (create_model/apply_op/model_to_bytes/layout_and_render)",
     "requires_raw": "FC-2", "acceptance_prose": "stub Vec<String> model compiles to WASM; JS inserts 1 op, reads back 1 paragraph."},
    {"id": "FC-4", "title": "Frontend command-router (replaces TipTap/WASM split)",
     "requires_raw": "—", "acceptance_prose": "unit test registers fake router, dispatches {command:'bold'}, asserts receipt; editor-common pnpm test green."},
]

# ---------------------------------------------------------------------------
# Scope map: exact file paths each task may edit.
# Paths are relative to server/. "!" prefix marks files the task must NOT
# regress (advisory). Derived from the plan §2-13 contract sections.
# ---------------------------------------------------------------------------
SCOPES: dict[str, dict] = {
    # --- Foundation contracts (FC) ---
    "FC-1": {
        "scope": ["core/crates/wo-common/src/path.rs", "core/crates/wo-common/src/lib.rs"],
        "section": "§2.1", "engine": "foundation",
        "accept": "cargo test -p wo-common path::",
    },
    "FC-2": {
        "scope": ["core/crates/wo-common/src/op.rs", "core/crates/wo-common/src/lib.rs"],
        "section": "§2.2", "engine": "foundation",
        "accept": "cargo test -p wo-common op::",
    },
    "FC-3": {
        "scope": ["core/crates/wo-renderer-wasm/src/lib.rs", "core/crates/wo-renderer-wasm/src/stub_model.rs"],
        "section": "§2.3", "engine": "foundation",
        "accept": "wasm-pack build core/crates/wo-renderer-wasm --target web --dev",
    },
    "FC-4": {
        "scope": ["packages/editor-common/src/core/command-router.ts",
                  "packages/editor-common/src/core/__tests__/command-router.test.ts"],
        "section": "§2.4", "engine": "foundation",
        "accept": "pnpm --filter @world-office/editor-common test -- command-router",
    },
    # --- DM (document mutation) ---
    "DM-0": {"scope": ["openspec/changes/rebuild-doc-mutation-engine/"], "section": "§3.5",
             "engine": "DM", "accept": "openspec validate rebuild-doc-mutation-engine"},
    "DM-1": {"scope": ["core/crates/wo-ooxml/src/model.rs", "core/crates/wo-ooxml/src/parser.rs",
                       "core/crates/wo-ooxml/src/serializer.rs",
                       "core/crates/wo-docx-renderer/src/layout.rs",
                       "core/crates/wo-renderer-wasm/src/lib.rs"],
             "section": "§3.4", "engine": "DM",
             "accept": "cargo test --workspace --lib && cd ../wo-test-harness && cargo run -p wo-conformance -- 06-font-times"},
    "DM-2": {"scope": ["core/crates/wo-ooxml-ops/"], "section": "§3.1", "engine": "DM",
             "accept": "cargo build -p wo-ooxml-ops"},
    "DM-3": {"scope": ["core/crates/wo-ooxml-ops/src/text.rs",
                       "core/crates/wo-ooxml-ops/src/text/tests.rs"], "section": "§3.5", "engine": "DM",
             "accept": "cargo test -p wo-ooxml-ops text::"},
    "DM-4": {"scope": ["core/crates/wo-ooxml-ops/src/format.rs"], "section": "§3.5", "engine": "DM",
             "accept": "cargo test -p wo-ooxml-ops format::"},
    "DM-5": {"scope": ["core/crates/wo-ooxml-ops/src/paragraph.rs"], "section": "§3.5", "engine": "DM",
             "accept": "cargo test -p wo-ooxml-ops paragraph::"},
    "DM-6": {"scope": ["core/crates/wo-ooxml-ops/src/table.rs"], "section": "§3.5", "engine": "DM",
             "accept": "cargo test -p wo-ooxml-ops table::"},
    "DM-7": {"scope": ["core/crates/wo-ooxml-ops/src/image.rs",
                       "core/crates/wo-ooxml/src/model.rs"], "section": "§3.5", "engine": "DM",
             "accept": "cargo test -p wo-ooxml-ops image::"},
    "DM-8": {"scope": ["core/crates/wo-ooxml-ops/src/list.rs",
                       "core/crates/wo-ooxml-ops/src/section.rs"], "section": "§3.5", "engine": "DM",
             "accept": "cargo test -p wo-ooxml-ops list:: section::"},
    "DM-9": {"scope": ["core/crates/wo-ooxml-ops/src/model.rs"], "section": "§3.5", "engine": "DM",
             "accept": "cargo test -p wo-ooxml-ops editable_model::"},
    "DM-10": {"scope": ["core/crates/wo-renderer-wasm/src/lib.rs",
                        "apps/web/apps/documenteditor-react/src/__tests__/apply-op.test.ts"],
              "section": "§4", "engine": "DM",
              "accept": "wasm-pack build core/crates/wo-renderer-wasm --target web --dev"},
    "DM-11": {"scope": ["packages/editor-common/src/core/command-router.ts",
                        "apps/web/apps/documenteditor-react/src/lib/rte-command.ts",
                        "apps/web/apps/documenteditor-react/src/components/DocumentHolder.tsx",
                        "packages/editor-common/src/ribbon/components/ControlRenderer.tsx"],
              "section": "§5", "engine": "DM",
              "accept": "pnpm --filter @world-office/documenteditor-react lint typecheck test"},
    "DM-12": {"scope": ["apps/web/apps/documenteditor-react/src/main.tsx",
                        "apps/web/apps/documenteditor-react/vite.config.ts"],
              "section": "§5", "engine": "DM",
              "accept": "pnpm --filter @world-office/documenteditor-react typecheck build"},
    # --- TL (text layout) ---
    "TL-1": {"scope": ["core/crates/wo-docx-renderer/src/layout.rs"], "section": "§4", "engine": "TL",
             "accept": "cargo test -p wo-docx-renderer multicolumn", "manual": True},
    "TL-2": {"scope": ["core/crates/wo-docx-renderer/src/layout.rs"], "section": "§4", "engine": "TL",
             "accept": "cargo test -p wo-docx-renderer table_row_break", "manual": True},
    "TL-3": {"scope": ["core/crates/wo-docx-renderer/src/layout.rs",
                       "core/crates/wo-ooxml/src/model.rs"], "section": "§4", "engine": "TL",
             "accept": "cargo test -p wo-docx-renderer header_footer::"},
    "TL-4": {"scope": ["core/crates/wo-docx-renderer/src/layout.rs"], "section": "§4", "engine": "TL",
             "accept": "cargo test -p wo-docx-renderer footnote::"},
    "TL-5": {"scope": ["core/crates/wo-docx-renderer/src/layout.rs"], "section": "§4", "engine": "TL",
             "accept": "cargo test -p wo-docx-renderer wrap_mode::", "manual": True},
    "TL-6": {"scope": ["core/crates/wo-docx-renderer/src/layout.rs"], "section": "§4", "engine": "TL",
             "accept": "cargo test -p wo-docx-renderer tab_stop::"},
    "TL-7": {"scope": ["core/crates/wo-docx-renderer/src/layout.rs"], "section": "§4", "engine": "TL",
             "accept": "cargo test -p wo-docx-renderer hyphenation::", "manual": True},
    # --- FL (formula) ---
    "FL-1": {"scope": ["core/crates/wo-formula/src/lexer.rs",
                       "core/crates/wo-formula/src/parser.rs",
                       "core/crates/wo-formula/src/ast.rs"], "section": "§5", "engine": "FL",
             "accept": "cargo test -p wo-formula grammar::"},
    "FL-2": {"scope": ["core/crates/wo-formula/src/eval.rs"], "section": "§5", "engine": "FL",
             "accept": "cargo test -p wo-formula eval_basic::"},
    "FL-3": {"scope": ["core/crates/wo-formula/src/functions/"], "section": "§5", "engine": "FL",
             "accept": "cargo test -p wo-formula functions::"},
    "FL-4": {"scope": ["core/crates/wo-formula/src/dep_graph.rs"], "section": "§5", "engine": "FL",
             "accept": "cargo test -p wo-formula dep_graph::"},
    "FL-5": {"scope": ["core/crates/wo-formula/src/recalc.rs"], "section": "§5", "engine": "FL",
             "accept": "cargo test -p wo-formula recalc::"},
    # --- SS (spreadsheet) ---
    "SS-1": {"scope": ["core/crates/wo-sheet/src/model.rs"], "section": "§6", "engine": "SS",
             "accept": "cargo test -p wo-sheet model::"},
    "SS-2": {"scope": ["core/crates/wo-sheet/src/ops.rs"], "section": "§6", "engine": "SS",
             "accept": "cargo test -p wo-sheet ops::"},
    "SS-3": {"scope": ["core/crates/wo-sheet/src/format.rs"], "section": "§6", "engine": "SS",
             "accept": "cargo test -p wo-sheet format::"},
    "SS-4": {"scope": ["core/crates/wo-sheet/src/conditional.rs"], "section": "§6", "engine": "SS",
             "accept": "cargo test -p wo-sheet conditional::"},
    "SS-5": {"scope": ["core/crates/wo-sheet/src/pivot.rs"], "section": "§6", "engine": "SS",
             "accept": "cargo test -p wo-sheet pivot::"},
    "SS-6": {"scope": ["core/crates/wo-sheet/src/validation.rs"], "section": "§6", "engine": "SS",
             "accept": "cargo test -p wo-sheet validation::"},
    "SS-7": {"scope": ["core/crates/wo-renderer-wasm/src/lib.rs"], "section": "§2.3", "engine": "SS",
             "accept": "wasm-pack build core/crates/wo-renderer-wasm --target web --dev"},
    "SS-8": {"scope": ["apps/web/apps/spreadsheeteditor-react/src/components/RightMenu/"],
             "section": "§6", "engine": "SS",
             "accept": "pnpm --filter @world-office/spreadsheeteditor-react lint typecheck test"},
    # --- SL (presentation) ---
    "SL-1": {"scope": ["core/crates/wo-slide/src/model.rs"], "section": "§7", "engine": "SL",
             "accept": "cargo test -p wo-slide model::"},
    "SL-2": {"scope": ["core/crates/wo-slide/src/ops.rs"], "section": "§7", "engine": "SL",
             "accept": "cargo test -p wo-slide ops::"},
    "SL-3": {"scope": ["core/crates/wo-slide/src/geometry.rs"], "section": "§7", "engine": "SL",
             "accept": "cargo test -p wo-slide geometry::"},
    "SL-4": {"scope": ["core/crates/wo-slide/src/animation.rs"], "section": "§7", "engine": "SL",
             "accept": "cargo test -p wo-slide animation::"},
    "SL-5": {"scope": ["core/crates/wo-slide/src/chart_embed.rs"], "section": "§7", "engine": "SL",
             "accept": "cargo test -p wo-slide chart_embed::"},
    "SL-6": {"scope": ["core/crates/wo-renderer-wasm/src/lib.rs"], "section": "§2.3", "engine": "SL",
             "accept": "wasm-pack build core/crates/wo-renderer-wasm --target web --dev"},
    "SL-7": {"scope": ["apps/web/apps/presentationeditor-react/src/components/RightMenu/"],
             "section": "§7", "engine": "SL",
             "accept": "pnpm --filter @world-office/presentationeditor-react lint typecheck test"},
    # --- CH (chart) ---
    "CH-1": {"scope": ["core/crates/wo-chart/src/model.rs"], "section": "§8", "engine": "CH",
             "accept": "cargo test -p wo-chart model::"},
    "CH-2": {"scope": ["core/crates/wo-chart/src/render.rs"], "section": "§8", "engine": "CH",
             "accept": "cargo test -p wo-chart render_bar_line_pie::", "manual": True},
    "CH-3": {"scope": ["core/crates/wo-chart/src/render.rs"], "section": "§8", "engine": "CH",
             "accept": "cargo test -p wo-chart render_scatter_area_radar::", "manual": True},
    "CH-4": {"scope": ["core/crates/wo-renderer-wasm/src/lib.rs"], "section": "§2.3", "engine": "CH",
             "accept": "wasm-pack build core/crates/wo-renderer-wasm --target web --dev"},
    # --- RT (routing) ---
    "RT-1": {"scope": ["core/crates/wo-route/src/anchor.rs"], "section": "§9", "engine": "RT",
             "accept": "cargo test -p wo-route anchor::"},
    "RT-2": {"scope": ["core/crates/wo-route/src/orthogonal.rs"], "section": "§9", "engine": "RT",
             "accept": "cargo test -p wo-route orthogonal::"},
    "RT-3": {"scope": ["core/crates/wo-route/src/astar.rs"], "section": "§9", "engine": "RT",
             "accept": "cargo test -p wo-route astar::"},
    "RT-4": {"scope": ["core/crates/wo-route/src/bezier.rs"], "section": "§9", "engine": "RT",
             "accept": "cargo test -p wo-route bezier::"},
    "RT-5": {"scope": ["apps/web/apps/visioeditor-react/src/components/"], "section": "§9", "engine": "RT",
             "accept": "pnpm --filter @world-office/visioeditor-react lint typecheck test"},
    # --- PDF ---
    "PDF-1": {"scope": ["core/crates/wo-pdf-render/Cargo.toml",
                        "core/crates/wo-pdf-render/build.rs"], "section": "§10", "engine": "PDF",
              "accept": "cargo build -p wo-pdf-render"},
    "PDF-2": {"scope": ["core/crates/wo-pdf-render/src/renderer.rs"], "section": "§10", "engine": "PDF",
              "accept": "cargo test -p wo-pdf-render render_page::", "manual": True},
    "PDF-3": {"scope": ["core/crates/wo-pdf-render/src/text.rs",
                        "core/crates/wo-pdf-render/src/annotation.rs"], "section": "§10", "engine": "PDF",
              "accept": "cargo test -p wo-pdf-render text:: annotation::"},
    "PDF-4": {"scope": ["core/crates/wo-pdf-render/src/acroform.rs"], "section": "§10", "engine": "PDF",
              "accept": "cargo test -p wo-pdf-render acroform::"},
    "PDF-5": {"scope": ["core/crates/wo-renderer-wasm/src/lib.rs"], "section": "§2.3", "engine": "PDF",
              "accept": "wasm-pack build core/crates/wo-renderer-wasm --target web --dev"},
    "PDF-6": {"scope": ["apps/web/apps/pdfeditor-react/src/components/RightMenu/"], "section": "§10",
              "engine": "PDF",
              "accept": "pnpm --filter @world-office/pdfeditor-react lint typecheck test"},
    # --- CO (collaboration) ---
    "CO-1": {"scope": ["services/coauthoring-service/src/model_op.rs"], "section": "§11", "engine": "CO",
             "accept": "cargo test -p coauthoring-service model_op_schema::"},
    "CO-2": {"scope": ["services/coauthoring-service/src/document.rs"], "section": "§11", "engine": "CO",
             "accept": "cargo test -p coauthoring-service merge::"},
    "CO-3": {"scope": ["services/coauthoring-service/src/cursor.rs"], "section": "§11", "engine": "CO",
             "accept": "cargo test -p coauthoring-service cursor::"},
    "CO-4": {"scope": ["services/coauthoring-service/src/replay.rs"], "section": "§11", "engine": "CO",
             "accept": "cargo test -p coauthoring-service replay::"},
    "CO-5": {"scope": ["packages/sdk-bridge/src/collaboration-client.ts"], "section": "§11", "engine": "CO",
             "accept": "pnpm --filter @world-office/sdk-bridge test"},
    "CO-6": {"scope": ["services/coauthoring-service/src/integration.rs"], "section": "§11", "engine": "CO",
             "accept": "cargo test -p coauthoring-service integration::"},
    # --- SP (spellcheck) ---
    "SP-1": {"scope": ["core/crates/wo-spell/src/aff.rs"], "section": "§12", "engine": "SP",
             "accept": "cargo test -p wo-spell aff::"},
    "SP-2": {"scope": ["core/crates/wo-spell/src/dic.rs",
                       "core/crates/wo-spell/src/suggest.rs"], "section": "§12", "engine": "SP",
             "accept": "cargo test -p wo-spell suggest::"},
    "SP-3": {"scope": ["core/crates/wo-spell/src/hyphenate.rs"], "section": "§12", "engine": "SP",
             "accept": "cargo test -p wo-spell hyphenate::"},
    "SP-4": {"scope": ["core/crates/wo-renderer-wasm/src/lib.rs",
                       "apps/web/apps/documenteditor-react/src/hooks/useSpellchecker.ts"],
             "section": "§12", "engine": "SP",
             "accept": "wasm-pack build core/crates/wo-renderer-wasm --target web --dev"},
}


def expand_requires(raw: str) -> list[str]:
    """Expand 'DM-2..8' → [DM-2,...,DM-8]; 'DM-2, SS-1' → both; '—' → []."""
    raw = raw.strip()
    if raw in ("—", "-", ""):
        return []
    deps: list[str] = []
    for token in re.split(r"[,/]", raw):
        token = token.strip()
        if not token:
            continue
        m = DEP_RANGE_RE.match(token)
        if m:
            prefix = m.group(1)
            base, _, n = prefix.partition("-")
            start = int(n)
            end = int(m.group(2))
            deps.extend(f"{base}-{i}" for i in range(start, end + 1))
        else:
            deps.append(token)
    return deps


def parse_plan(plan_path: Path) -> list[dict]:
    """Extract task rows from the plan markdown."""
    rows: list[dict] = []
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        if not TASK_ID_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        task_id, title, requires, acceptance = cells[0], cells[1], cells[2], cells[3]
        # strip markdown emphasis from title
        title = re.sub(r"\*\*(.+?)\*\*", r"\1", title)
        rows.append({
            "id": task_id,
            "title": title,
            "requires_raw": requires,
            "acceptance_prose": acceptance,
        })
    return rows


def build_manifest(plan_path: Path) -> dict:
    rows = FOUNDATION_TASKS + parse_plan(plan_path)
    tasks: list[dict] = []
    missing_scope: list[str] = []
    seen: set[str] = set()

    for row in rows:
        tid = row["id"]
        seen.add(tid)
        meta = SCOPES.get(tid)
        if meta is None:
            missing_scope.append(tid)
            meta = {"scope": [], "section": "?", "engine": "?", "accept": ""}
        tasks.append({
            "id": tid,
            "engine": meta.get("engine", "?"),
            "title": row["title"],
            "section": meta.get("section", "?"),
            "deps": expand_requires(row["requires_raw"]),
            "scope": meta.get("scope", []),
            "accept": meta.get("accept", ""),
            "acceptance_prose": row["acceptance_prose"],
            "manual": meta.get("manual", False),
        })

    # Catch scope-map entries with no matching plan row (stale)
    stale = sorted(set(SCOPES) - seen)

    return {
        "_meta": {
            "source": str(plan_path.relative_to(WORKSPACE_ROOT)) if plan_path.is_relative_to(WORKSPACE_ROOT) else str(plan_path),
            "task_count": len(tasks),
            "engines": sorted({t["engine"] for t in tasks}),
            "missing_scope": missing_scope,
            "stale_scope_entries": stale,
        },
        "tasks": tasks,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    args = ap.parse_args()

    if not args.plan.is_file():
        print(f"ERROR: plan not found: {args.plan}", file=sys.stderr)
        return 2

    manifest = build_manifest(args.plan)
    meta = manifest["_meta"]
    print(f"Parsed {meta['task_count']} tasks across {len(meta['engines'])} engines: {', '.join(meta['engines'])}")
    if meta["missing_scope"]:
        print(f"WARN: {len(meta['missing_scope'])} tasks have no scope entry: {', '.join(meta['missing_scope'])}", file=sys.stderr)
    if meta["stale_scope_entries"]:
        print(f"WARN: stale scope entries (no plan row): {', '.join(meta['stale_scope_entries'])}", file=sys.stderr)

    if args.check:
        return 1 if (meta["missing_scope"] or meta["stale_scope_entries"]) else 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
