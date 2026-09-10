# opencloud-docserver — agent notes

Editor location: `web/editor.js` (one IIFE). Endpoint surface: `src/editor/router.py`.

## wo-command bus — the agent API

The editor exposes every mutating editor operation as a command on the
`wo-command` DOM event bus. `emitCommand(cmd, value)` (editor.js) dispatches:

```js
frame.contentWindow.dispatchEvent(new CustomEvent("wo-command", {
  detail: { command: "bold", value: null },   // value: string | null
}));
```

The editor listens on `window` and routes each event through `runCommand()`
(editor.js ~684). Same-origin only (the editor iframe has no postMessage
bridge). Everything below is the complete command surface; anything not
listed here is NOT on the bus (save, dialogs, find bar, AI propose are DOM
buttons — drive them via `document.querySelector(...).click()` or the
`/api/...` HTTP surface).

### Guarantees (all commands)

- **Read-only no-op**: when `window.__READ_ONLY__`, mutating commands do
  nothing.
- **Undoable**: every command lands as one `captureHistory()` step (custom
  branches call it explicitly; native `execCommand` paths arm it because
  structural commands fire no guaranteed `input` event).
- **Collab + autosave wired**: custom branches arm `markDirty()` +
  `scheduleCollabSync()` + `notifyHost()`; native paths arm `markDirty()` +
  `captureHistory()` (list/line-height/style branches arm the full chain).
- **Selection-scoped**: inline/block commands act on the current selection
  (collapsed caret = insertion point). The bus does not set selections —
  seed one via `document.querySelector('#editor')` ranges first if needed.

### Commands

**Custom branches (handled before any `execCommand`)**

| command | value | effect |
|---|---|---|
| `insertUnorderedList` / `insertOrderedList` | — | toggle list under selection (custom `toggleList`, round-trips to DOCX/ODT) |
| `undo` / `redo` | — | walk the explicit snapshot chain (not native undo) |
| `lineHeight` | `"1"`,`"1.15"`,`"1.5"`,`"2"`… | set `line-height` on block(s) under selection; `1`/invalid clears |
| `directionRtl` | — | toggle `direction: rtl` on selected block(s) |
| `underlineDouble` / `strikeDouble` | — | wrap selection in span with doubled `text-decoration` |
| `listStyle` | `"disc"`,`"circle"`,`"square"`,`"decimal"`,`"lower-alpha"`,`"lower-roman"`,`"upper-alpha"` | restyle (or create) the list under the selection |
| `code` | — | toggle monospace span (falls back to `fontName` execCommand with no selection) |
| `smallCaps` / `allCaps` | — | toggle `font-variant`/`text-transform` span (strikeThrough fallback) |
| `insertHR` | — | `<hr/>` at caret |
| `insertPageBreak` | — | `div.page-break` + trailing `<p>` (marker contract) |
| `insertSectionBreak` | — | `hr.section-break` + trailing `<p>` (→ w:sectPr) |
| `insertFootnote` / `insertEndnote` | — | citation `<sup>` + body `<span>` at caret |
| `insertPageNumber` | — | `span.page-number` (→ PAGE field) |
| `insertHeader` / `insertFooter` | — | one `header.page-header` (body start) / `footer.page-footer` (body end) per doc; re-issue focuses the existing one |
| `insertSymbol` | the character | insert text at caret |
| `insertDate` | — | insert `YYYY-MM-DD` |

**`formatBlock`** — value `"H1"…` `"H6"`, `"P"` (case-insensitive; canonical
lowercase passed to the engine). Replaces the block under the caret.

**Span styles** (`fontSize`, `fontName`, `foreColor`, `hiliteColor`,
`backColor`) — run with `styleWithCSS` on, so the result is `span[style]`
(properties the save sanitizer whitelists). Values: CSS sizes/colors/
families, e.g. `"4"`/`"red"`/`"Consolas"`.

**Native pass-through** (fall straight to `document.execCommand`): `bold`,
`italic`, `underline`, `strikeThrough`, `superscript`, `subscript`,
`indent`, `outdent`, `justifyLeft`, `justifyCenter`, `justifyRight`,
`justifyFull`, `removeFormat`.

### Not on the bus (drive via DOM/HTTP)

- **Save**: `#btn-save` click, Ctrl+S, or 30 s autosave → `POST
  /api/documents/{id}/save` `{html}` (the same HTML `export` round-trips).
- **AI propose**: `#btn-ai-grammar` / `#btn-ai-assistant` → dialog →
  `POST /api/documents/{id}/ai/propose` `{instruction, model}` → tracked
  spans; accept/reject via the review panel (`#review-list .review-item
  button.primary`). Agents may also post ops directly:
  `POST /api/documents/{id}/collab/ops` with an `agent=<name>` client id
  (see `src/ai/tools.py` for the op schema).
- **Dialogs** (find, symbol, image, link…): open via their toolbar buttons;
  plain DOM.
