# orgono

A deterministic codebase cartographer. It walks a repository's syntax trees with
`tree-sitter`, builds a typed knowledge graph, answers impact-analysis questions
from the terminal, and renders the whole thing as an interactive 3D map served on
localhost.

It works completely offline. No API key, no network, no account.

```bash
pip install orgono          # one command, every supported language, any OS

orgono map                  # build the graph
orgono view                 # 3D map + assistant at http://127.0.0.1:7373
orgono stacks               # what the repo is built with
orgono impact parse_config --direction callers
orgono path get_user_route query_users
orgono ask "what calls redact_text?"
```

Prefer an isolated install for a command-line tool:

```bash
pipx install orgono         # keeps orgono out of your system Python
```

**Requirements:** Python 3.10+ and nothing else. No compiler, no Node, no
Docker, no database, no account.

---

## What it actually does

`orgono map` parses every supported source file, extracts definitions, imports
and call sites, and writes a sorted JSON graph to `.orgono/graph.json`.

**Node kinds:** `file`, `function`, `method`, `class`, `interface`, `external`,
`unparsed`.
**Edge types:** `defines`, `imports`, `calls`, `references`. Every edge carries
the path, line and column it came from, so any answer traces back to source.

**Languages — 18, explicit.** Anything else is reported as `unsupported`,
never half-parsed:

`bash` · `c` · `c_sharp` · `cpp` · `go` · `java` · `javascript` · `kotlin` ·
`lua` · `php` · `python` · `ruby` · `rust` · `scala` · `sql` · `swift` ·
`typescript` · `tsx`

Every grammar ships as a prebuilt wheel for Linux, macOS (Apple Silicon and
Intel) and Windows, so nothing compiles and nothing downloads at run time.
`orgono doctor` lists what is ready on your machine. If a grammar ever fails to
load, that file is reported as `unsupported` with the exact `pip install`
command that fixes it — the run continues.

Real numbers from this machine, mapping 1,399 files of third-party Python
(197 MB of `site-packages`):

```
nodes 33645   edges 241806
kinds class=3249 external=3620 file=1399 function=9700 interface=396 method=15281
edges calls=155736 defines=29935 imports=10577 references=45558
files parsed=1399 skipped=3 unsupported=221
took  7170 ms
```

The three skipped files were 3.4 MB, 3.2 MB and 1.1 MB vendored bundles, each
skipped with a logged reason rather than parsed.

## Commands

| command | what it does |
|---|---|
| `orgono map` | build the graph into `.orgono/graph.json` |
| `orgono view` | 3D map **with the assistant inside it**, on `127.0.0.1:7373` |
| `orgono find <name>` | find symbols by name, kind, language or path |
| `orgono impact <symbol>` | what reaches, or is reached by, a symbol |
| `orgono path <a> <b>` | the shortest typed edge chain connecting two symbols |
| `orgono file <path>` | definitions and edges for one file |
| `orgono stats` | counts by kind, edge type and language |
| `orgono stacks` | frameworks, build systems, databases and infra in use |
| `orgono report` | files skipped, partial, unparsed or unsupported |
| `orgono ask "<question>"` | grounded Q&A (local by default) |
| `orgono tools` | the tool surface offered to sibling agents |
| `orgono auth` | API key status / entry |
| `orgono models` | list models reachable through OpenRouter |
| `orgono doctor` | grammars, limits and egress posture |
| `orgono validate` | run the OpenGAP validator |

## The 3D map

`orgono view` serves a WebGL map built directly on a vendored three.js — no CDN,
no network.

- **Barnes-Hut octree** N-body layout (O(n log n)), decoupled from the render
  loop so the graph settles at the same wall-clock speed on software rendering.
- **Impact focus** — click any node to trace its neighbourhood to a chosen depth.
  Everything outside the subgraph dims rather than disappearing, so you keep your
  bearings. `Isolate` hides the rest entirely.
- Edge-type and node-kind filters, live symbol search, adjustable spread and link
  distance, auto-framing, and labels that stay a constant size on screen.
- Drag to orbit, right-drag to pan, scroll to zoom, `/` to search, `f` to frame,
  `esc` to clear.

### The assistant lives inside the map

The chat panel is not a separate tool bolted on: **an answer selects the nodes it
cites**, so asking a question moves the camera and lights up the subgraph the
answer is about. Citations are clickable and focus the node.

Ask *"how does extract_repo connect to redact_text?"* and you get the call chain
with `path:line` for every hop, the relevant subgraph highlighted in 3D, and a
count of any secrets that were redacted from the snippets.

It answers **locally, from the graph** — no model, no network, whatever your
egress settings say. `orgono view --no-assistant` serves the map read-only.

**Why this endpoint is locked down:** a server on localhost that answers
questions about your code is reachable by every page your browser loads. Four
things prevent a drive-by from reading your code map, each verified by a test
that tries to get past it:

| control | behaviour |
|---|---|
| per-run bearer token | fresh 43-char token each run, injected into the page, never in a URL |
| Origin/Host check | a request from any other site is refused with `403`, even with a valid token |
| body-size cap | over 8 KB is refused with `413` |
| rate limit | 60 requests/minute |

Responses use the same `QueryCaps` as the CLI, so an authorised caller still
cannot sweep the graph.

## Asking questions

`orgono ask` retrieves deterministically from the graph, then optionally hands
that retrieved context to a model.

```bash
orgono ask "how does redact_text connect to extract_repo?"
```

With no key and no network this prints the structural answer — the actual call
chain, with citations:

```
connection found:
  extract_repo  -[calls]->  info        orgono/app/core/extract.py:443
  info          -[calls]->  emit        orgono/app/core/obs.py:71
  emit          -[calls]->  redact_obj  orgono/app/core/obs.py:61
  redact_obj    -[calls]->  redact_text orgono/app/core/redact.py:101
```

Every line of that was produced by Python, not by a model. The model is only ever
asked to turn retrieved, redacted, cited context into prose.

## Using a model (optional, off by default)

Orgono **never stores your API key**. It reads one from, in order:

1. `--api-key` on the command line
2. `ORGONO_OPENROUTER_API_KEY` or `OPENROUTER_API_KEY` in the environment
3. a `.env` file *you* maintain

```bash
export ORGONO_OPENROUTER_API_KEY=sk-or-v1-...
# or keep it in a .env you control:
orgono auth login --save-env      # the only thing that writes; also gitignores .env
orgono auth status                # where the key is coming from
```

Any OpenRouter model id works — there is no allowlist:

```bash
orgono models --free
orgono ask "why is this parser bounded?" --model meta-llama/llama-3.3-70b-instruct \
  --send --enable-egress --no-dry-run
```

Sending is deliberately awkward, because it is the moment private source code
leaves your machine. Three independent switches are required, and without
`--send` you get a dry run printing the exact payload:

```bash
orgono explain "what is this?" --about query_users     # prints, sends nothing
```

**Costs.** Local mapping, querying and the 3D map are free and offline forever.
Only `--send` costs money, bounded by `ORGONO_MAX_SPEND_USD` (default $0.50) and
a token ceiling enforced in Python before any request is built.

## What this does to your machine

orgono runs on your personal files, so here is precisely what it touches. Every
line below is asserted by `tests/test_install_safety.py`, not just claimed.

**It writes exactly one place:** `<repo>/.orgono/` — the graph and its cache.
Nothing is written to your home directory, your shell profile, or anywhere
outside the repository you point it at. That directory ignores itself (orgono
drops a `.gitignore` containing `*` inside it), so a map of your private
codebase is never committed by accident. Your own `.gitignore` is never touched.

**The graph stores structure, not content.** `graph.json` holds symbol names,
file paths and line numbers — never source code and never literal values. A
line like `DB_PASSWORD = "hunter2"` is recorded as a constant named
`DB_PASSWORD`; the value is not stored anywhere. It also records the repository
directory *name*, never its absolute path, because an absolute path carries your
OS username and folder layout and `graph.json` is a file people share.

**It makes no network connection** unless you pass `--send` together with
`--enable-egress` and `--no-dry-run`. The local pipeline has no HTTP client:
`requests` is imported lazily and only inside the egress path.

**Secrets are stripped before anything leaves.** Snippets are redacted the
moment they are read, and the dry-run payload reports how many secrets were
removed. If that count says 3, three were removed; if it says 0, your code was
clean. (It reported 0 unconditionally until an audit caught it — see below.)

**It never stores your API key.** There is no credentials file. The key is read
from a flag, the environment, or a `.env` you maintain, and held in memory for
one command.

**It runs no code from your repository.** It parses files into syntax trees and
reads manifests as data. There is no `eval`, `exec`, `os.system`, `pickle`, or
`shell=True` anywhere in the package. The only subprocess in the whole tool is
`orgono validate`, which invokes the `opengap` binary with list arguments and no
shell.

**It has no install-time code execution**, no `setup.py`, no postinstall hook,
and exactly one console entry point (`orgono`). There is no telemetry, no
analytics, no crash reporting and no auto-update.

**It binds to loopback.** `orgono view` serves on `127.0.0.1` only, verified by
attempting a connection from a non-loopback interface. Path traversal is refused
(tested against encoded, doubled and backslash variants). The 3D viewer loads a
vendored copy of three.js from disk and fetches nothing from the internet, and
its payload carries no source code.

**Model replies are display-only.** Repository content reaches the model when you
opt into egress, so its reply is untrusted input: orgono prints it and nothing
else. There is no path from a model response to a file write, a shell command or
a further tool call.

**Footprint:** about 40 MB installed — 1 MB of orgono and 39 MB of compiled
grammars for 18 languages. That size is the cost of working entirely offline;
the alternative would download grammars on first use.

To remove it completely: `pip uninstall orgono`, then delete any `.orgono/`
directories in repositories you mapped.

## Safety posture

- Egress off by default; dry-run on even once enabled; a key on disk is not consent.
- Secrets are redacted from snippets and log lines before they are emitted, with
  planted-credential tests covering key shapes from OpenAI, GitHub, Slack, Google,
  AWS, Stripe, JWTs, PEM blocks and connection URLs.
- Writes never leave `<repo>/.orgono/`; snippet reads never escape the repo root.
- The agent tool surface is five named operations with typed parameters. There is
  no free-form instruction field. Queries are capped in code, and one that would
  sweep most of the graph is refused rather than truncated.
- Every query is logged with its caller, parameters and how much it returned.

## Platforms

Tested in CI on Linux, macOS and Windows across Python 3.10–3.13, including a
clean wheel install on each. Paths inside the graph are always forward-slashed,
so a `graph.json` built on Windows matches one built on Linux byte for byte.

## Configuration

All bounds are environment variables with laptop-safe defaults:

| variable | default |
|---|---|
| `ORGONO_MAX_FILE_BYTES` | 1048576 |
| `ORGONO_MAX_FILES` | 5000 |
| `ORGONO_MAX_DEPTH` | 25 |
| `ORGONO_MAX_WALL_SECONDS` | 120 |
| `ORGONO_QUERY_MAX_NODES` | 200 |
| `ORGONO_QUERY_MAX_SNIPPET_LINES` | 12 |
| `ORGONO_EGRESS_ENABLED` | 0 |
| `ORGONO_MAX_SPEND_USD` | 0.50 |

`orgono doctor` prints the live values.

## Known limitations

Read these before trusting an answer.

- **Call resolution is by name, not by type.** Orgono cannot tell which `to_dict`
  a call means. It prefers a definition in the calling file; if a name is defined
  in more than four places it records a `references` edge instead of inventing
  `calls` edges. Dynamic dispatch, decorators, re-exports and runtime imports are
  missed entirely.
- **No cross-language edges.** A TypeScript `fetch` to a Python route is invisible.
  Despite the framing of the original brief, orgono **cannot** answer "what
  connects this API route to this database table" unless that connection happens
  to be a chain of same-named function calls in one language.
- **No type inference and no data-flow analysis.** It is a syntactic map.
- **Orgono does not use `graphify`.** Extraction is built directly on
  `tree-sitter` and the individual first-party grammar packages. Note that
  graphify *is* real ([Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify),
  published on PyPI as **`graphifyy`**) — an earlier draft of this README claimed
  it did not exist, because the bare name `graphify` on PyPI is registered with
  zero releases and the npm package of that name is an unrelated 2015 jQuery
  random-graph generator. That claim was wrong and has been corrected. Orgono is
  an independent implementation with a deliberately smaller scope.
- **Grammar packages, not `tree-sitter-language-pack`.** Version 1.20.0 of the
  language pack ships **zero** grammars and downloads them on first use, which
  would have broken the offline guarantee. Orgono depends on the individual
  `tree-sitter-<lang>` wheels, which bundle compiled grammars.
- **The OpenRouter integration is written against documented behaviour, not a
  live call.** `openrouter.ai` was unreachable from the build sandbox, so
  `orgono models` and `--send` are exercised against recorded fixtures and the
  refusal paths, not a real response. Treat that one path as unverified.
- **Lyzr is not used.** Nothing in the analysis needed an orchestrator, so
  shipping one would have been a stated capability that does not exist.
- `.env` parsing is intentionally minimal (`KEY=VALUE`, quotes, `export`,
  `#` comments). It is not a full dotenv implementation.

## Privacy audit

These were found by attacking the tool, not by reading it. Each is now a test in
`tests/test_privacy.py`.

| finding | severity | status |
|---|---|---|
| `graph.json` stored the **absolute repository path**, leaking the OS username and folder layout of whoever built it | medium | fixed — only the directory name is stored |
| `.orgono/` was **not self-ignoring**, so a map of a private codebase could be committed | medium | fixed — a self-ignoring `.gitignore` is written inside it |
| The dry-run reported **`redactions: 0`** even when secrets had been stripped, because snippets were redacted on read and the second pass found nothing left to count | medium | fixed — counted where it happens and carried through both the `explain` and `ask` paths |
| A **secret-shaped filename** is stored unredacted as a node name | low | **not fixed, by design** — the path must be real for snippets to resolve, and such a filename is already in the user's git history |

Verified clean and unchanged: no secret values on disk, `.env` never parsed,
symlinks skipped, path traversal refused, loopback-only binding, no source code
in the viewer payload, and no sink from model output to execution.

## Learning from graphify's bug tracker

Graphify solves a related problem and has a large public issue tracker, which is
a free list of the failure modes this kind of tool actually hits.
`tests/test_graphify_regressions.py` turns those into regression tests, each
naming the upstream issue it guards:

| upstream issue | failure mode | orgono's status |
|---|---|---|
| #3513 | a file without a trailing newline reported as a syntax error | passed already |
| #3472 | the same file via two path forms creating duplicate nodes | passed already |
| #3570, #3477 | incremental rebuild dropping cross-file edges | passed already |
| #3540 | extraction aborting on async/await, generics, records | passed already |
| #3565 | import edges dropped for a whole language | passed already |
| #3539 | every edge collapsing to one hardcoded type | passed already |
| #3548 | reported counts not matching the graph | passed already |
| #3511, #3504 | files set aside without being surfaced | passed already |
| #3485 | ambiguous symbol resolution silently guessing | passed already |
| **#3471** | **module-level `const`/`static` producing no nodes** | **was a real bug — fixed** |

The last row was a genuine gap: Rust `const`/`static`, Go `const`, TypeScript
`enum`/`type` and Java `enum` produced no nodes at all, so that part of a
module's public surface was invisible to impact analysis. Orgono now emits a
`constant` node kind and type declarations for those languages.

## Identity (OpenGAP)

```
$ opengap validate
Validating gitagent
i Directory: /home/user/Codebase-Cartographer
────────────────────────────────────────────────────────────
✓ agent.yaml — valid
✓ SOUL.md — valid
────────────────────────────────────────────────────────────
✓ Validation passed (0 warnings)
```

`agent.yaml` carries four scalar fields, which cannot express the real contract —
so the contract lives in `tests/` instead, where it is enforced: the tool surface
is asserted against the code, the limits against their defaults, and the egress
rules against the refusal paths.

## Development

```bash
pip install -e ".[dev]"
pytest -q          # 334 tests
ruff check .
python tools/probe_grammars.py --captures   # re-verify grammar node names
```

Run `tools/probe_grammars.py` after any `tree-sitter` upgrade. Every query in
`orgono/app/core/languages.py` was written from its output, and if a grammar
renames a node type the language tests fail loudly rather than silently emptying
the graph.

## License

Apache-2.0. Vendored `three.js` is MIT (`orgono/web/vendor/three.LICENSE`).
