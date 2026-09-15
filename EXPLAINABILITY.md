# Explainability

## Decision Reasoning

Orgono decides what to traverse and what edges to emit using deterministic rules in `orgono/app/core/extract.py` — extension-to-grammar mapping, per-language tree-sitter queries, and a name-binding rule that prefers a definition in the calling file and refuses to guess when a name is defined in more than four places. No language model participates in building the graph, so the reasoning behind any edge is readable as code and reproducible from the same commit.

## Data Inputs

The only inputs are the source files of the repository you point it at, read as bytes and parsed into syntax trees by vendored tree-sitter grammars for Python, JavaScript, TypeScript, TSX, Go, Rust and Java. Nothing else is read — no git history, no environment beyond configuration variables, no network — and nothing is transmitted unless you explicitly enable egress and pass `--send`.

## Known Limitations

Call resolution is by name, not by type: Orgono cannot tell which `to_dict` a call means when several exist, so it prefers the same file and otherwise emits a `references` edge instead of a `calls` edge, which means real cross-module calls through dynamic dispatch, decorators, re-exports, or runtime imports are missed entirely. It has no cross-language edges (a TypeScript `fetch` to a Python route is invisible), no type inference, no data-flow analysis, and therefore cannot actually answer "what connects this API route to this database table" unless the connection happens to be a chain of same-named function calls.
