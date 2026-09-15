# Orgono

Orgono is a cartographer, not an oracle. It reads a repository's syntax trees and
draws a map: which symbols exist, where they are defined, and which ones reach
which others. It does not form opinions about the code, and it does not need a
language model to do its job. The map is produced by deterministic Python, so the
same commit always yields the same map, and any line of the map can be traced
back to a line of source.

## What Orgono decides

Orgono decides only mechanical things, and every one of them is a rule in code
that a test can check:

- **What to traverse.** Which files are source files (by extension, against an
  explicit list of seven supported languages), which directories are skipped
  (vendor bundles, build output, virtualenvs), and when to stop — on file size,
  file count, directory depth, total bytes read, or wall clock.
- **What edges to emit.** A `defines` edge from a file to each definition it
  contains; an `imports` edge for each import; a `calls` edge when a call site
  binds to a definition in the graph; a `references` edge when it does not. Every
  edge carries the path, line and column it came from.
- **How to bind an ambiguous name.** A definition in the calling file wins. If
  there is none and the name is defined in more than four places, Orgono records
  a `references` edge rather than inventing four `calls` edges it cannot justify.
- **What to rank first when answering.** Real definitions outrank `external`
  stubs, so an answer is about the repository rather than about `len` and
  `append`.

## What Orgono never decides

These are not defaults that a flag can quietly flip open. They are the shape of
the thing:

- **What leaves the machine.** Nothing does, unless a human passes
  `--enable-egress --no-dry-run --send`. The local pipeline never imports an HTTP
  client. Before any request, Orgono prints the exact payload it would send.
- **Whether something is a secret.** Orgono does not judge; it applies a fixed
  set of redaction rules to every snippet and every log line, and the rules are
  tested with planted credentials.
- **Where output goes.** Writes land in `<repo>/.orgono/` and nowhere else; a
  path that would escape the repository root raises instead of writing.
- **What a caller is allowed to ask for.** The tool surface is five named
  operations with typed parameters. There is no free-form instruction field,
  because a sibling agent can be prompt-injected and the capability worth abusing
  is bulk export of this repository.
- **Whether an answer is true.** Orgono reports what the AST says. When a model is
  involved it is given retrieved, redacted, cited context and is never the thing
  that decides whether a guarantee holds.

## Boundaries

- **Never sends source code off the machine by default.** Egress is off, dry-run
  is on even once it is enabled, and a key on disk is not consent.
- **Never stores your API key.** It is read from a flag, the environment, or a
  `.env` you maintain. `orgono auth login --save-env` is the only thing that
  writes, and only when you ask.
- **Never writes outside the repository's `.orgono/` directory**, and that
  directory ignores itself so a map of private code is never committed by
  accident. The user's own `.gitignore` is theirs, not Orgono's.
- **Never writes down where it ran.** The graph records the repository's
  directory name, never its absolute path: an absolute path carries the
  operator's username and folder layout, and the graph is a file people share.
- **Never stores source code or literal values.** The graph holds names,
  paths and line numbers. `DB_PASSWORD = "hunter2"` becomes a constant named
  `DB_PASSWORD`; the value is not recorded anywhere.
- **Never returns the whole graph.** Responses are capped in code, and a query
  whose selector sweeps most of the graph is refused rather than truncated.
- **Never hides a failure.** A file that cannot be parsed is recorded as
  `unparsed` with its error; one that parses with syntax errors is `partial`; a
  language Orgono does not support is `unsupported`. Silence is never an option,
  because a graph that quietly omits part of a repository produces confidently
  wrong impact analysis.
- **Never misreports what it removed.** The redaction count in an egress
  payload is the true number of secrets stripped, counted where the stripping
  happens. A security signal that reads zero when it should read three is worse
  than no signal at all.
- **Never acts on a model's answer.** Repository content reaches the model, so
  its reply is untrusted input: it is printed, never executed, written or
  followed.
- **Never lets logging break a run.** An audit write that fails is swallowed; the
  work that already succeeded still succeeds.
