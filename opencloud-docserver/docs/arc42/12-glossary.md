# 12 — Glossary

| Term | Meaning |
|------|---------|
| **WOPI** | Web Application Open Platform Interface — the HTTP contract file clouds use to open documents in editors (discovery, CheckFileInfo, locks, GetFile/PutFile). |
| **OpenCloud** | The self-hosted file cloud (OpenCloud EU fork of ocis) deployed at cloud.graphwiz.ai. |
| **CRDT / TextCRDT** | Conflict-free Replicated Data Type; here a sequence CRDT for plain text with `(site, seq)` Lamport-clock identity and a `ROOT` anchor. |
| **Hub (`CollabHub`)** | In-process collaboration engine: per-doc CRDT state, op log, presence, SSE fan-out. The only write path. |
| **Op** | One mutation record `{"t": "insert"|"delete", "s": site, "b": seq, …}` — the audit trail. |
| **Register / F-id** | The feature register `harness-graph/features.yaml` (wo-test-harness repo) (82 stable F-### ids); coverage claims require tagged tests or a divergence note. |
| **Divergence** | A documented, honest gap (e.g., "hyphenation absent after 4-step audit") recorded in the register instead of a false claim. |
| **Drift gate** | `seed.py --check`: repo files are truth; the committed `graph.json` projection must match exactly. |
| **check-register.py** | CI gate: every listed F-id must be test-covered or divergence-documented. |
| **Oracle / wo-conformance** | Rust harness scoring our render against LibreOffice truth and OnlyOffice renders (PDF/poppler geometry). |
| **Grounding pack** | `get_context` result: size-bounded text + line blocks + version tail + sha256; pure function of document state (E18S1). |
| **Agent** | An MCP client acting as a collaboration client with `agent=<name>` site identity. |
| **MCP** | Model Context Protocol — JSON-RPC 2.0 over stdio (NDJSON) in this deployment; no SDK used. |
| **Tool catalog (v1.1)** | The 6 versioned JSON-Schema tools: `get_context`, `read_doc`, `apply_ops`, `get_versions`, `lock`, `presence`. |
| **AgentRunner** | Thin loop: model callable → tool calls → ops; step/op budgets; structured `AgentReport`. |
| **Adapter / transport** | Pure vendor-response translator + injected `request → response` callable; no vendor SDK, no in-process egress. |
| **Loud failure** | Missing capability ⇒ typed error (e.g., export 500), never a silent placeholder. |
| **Lock-mismatch contract** | WOPI 409 semantics echoing the current lock token; identical for humans and agents. |
| **Golden (test)** | Committed expected artifact (catalog JSON, converter output); recapture only via review PR. |
| **ocstaging** | The staging stack (:9201) mirroring live topology; first stop for risky config A/Bs. |
