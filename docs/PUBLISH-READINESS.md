# Publish readiness

The plan (`PHASE1-PLAN.md`, `README.md`) is that this repository is **public under
Apache-2.0 from the first cut**. It was developed inside a private workspace, so
"publish-ready" is its own target, separate from "feature-complete." This page is
the checklist for that target: what is verified, and what is a decision — not a
code change — that only Rob can make.

The machine-checkable half is a script, not a habit:
`scripts/publish-scan.sh` (also `make publish-scan`, and a CI job named
`publish`). It greps the **tracked** tree — only tracked content ships — and
fails on three classes:

- **credentials** — private-key blocks, AWS/GitHub/Slack/OpenAI-style tokens;
- **operator home paths** — `/home/<user>/` for a real user (generic fixture
  users such as `/home/agent/`, `/home/dev/` are allowed);
- **private infrastructure** — RFC1918 `192.168.x.x` addresses and the private
  service ports. Private **hostnames** are not listed in the tracked script: it
  caught its own first draft naming them once the script became a tracked file,
  which is the whole failure mode. A maintainer keeps the real names in an
  untracked `.publish-denylist` (gitignored), one ERE per line, and the scan uses
  it when present — so the local run gates on the real names while the public CI
  gates the generic classes only. The public gate cannot name what is private.

It is deliberately narrow. It does not try to judge prose; it catches the classes
that are invisible in review and permanent once pushed.

The checklist below is partly machine-checkable, so one command runs it all:
`make preflight` (`scripts/preflight.sh`) executes every CI job's local
of-record — build, lint (incl. `pin-check`), `go vet -tags sqlite`, tests
(default and sqlite), demo, quickstart, container, publish-scan, deps, fuzz — and
stops at the first failure. "Push-ready" is that command's exit status, not
eight separate recollections of what passed. (It needs Docker and module
downloads, the same as CI.)

## Checklist

| Item | Status |
|---|---|
| `LICENSE` (Apache-2.0) | ✅ present |
| `README.md` (overview, build, run, known blind spots) | ✅ |
| `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md` | ✅ |
| CI: build, vet, test (default + sqlite), lint, demo, quickstart | ✅ |
| CI: container image built and checked (`make container-check`) | ✅ |
| CI: publish scan (`make publish-scan`) | ✅ |
| No credentials in the tracked tree | ✅ scan clean |
| No operator home path in the tracked tree | ✅ scan clean |
| No private infrastructure in the tracked tree | ✅ scan clean |
| Dependency licenses permissive (no copyleft/unknown) | ✅ `make deps-report` |
| Module path / project name | ✅ **Watchman** — decided 2026-10-01; module path `github.com/crperdue73/AEGIS-Watchman` |
| Internal process docs in the public tree | ✅ **held out** — `BRIEF.md` + `WORKLOG.md` excluded; Phase 0/1 docs ship (README-linked) |
| Copyright holder / `NOTICE` | ✅ `LICENSE` reads "Copyright 2026 CRPerdue Technologies, LLC"; no `NOTICE` needed |

## Open decisions for Rob

### 1. The name — ✅ DECIDED (2026-10-01)

**Watchman.** Rob chose it on 2026-10-01 and the rename is done in the tracked
tree: module path `github.com/crperdue73/AEGIS-Watchman` (`go.mod` + every import),
`Makefile` `PKG`, the four `cmd/*` binaries (`watchman-collector`,
`watchman-central`, `watchman-manifest`, `watchman-rules`), the Dockerfile image
labels, the deploy unit files (`deploy/watchman-{collector,central}.service`),
the event schema file (`watchman.event.schema.json`, wire version
`watchman.event/v1`), and every doc / example / script. `build`, `vet`,
`go test` (default + `sqlite`), `check-links`, `quickstart-check`,
`container-check`, and `publish-scan` are all green on the renamed tree. Nothing
had been pushed, so no consumer ever saw the old name — there is no
compatibility surface to carry.

The GitHub owner is **`crperdue73`** (Rob's account; **not** the separate
`crperdue` user, which owns nothing here) and the repo is **`AEGIS-Watchman`**,
so the module path is `github.com/crperdue73/AEGIS-Watchman`. The product name
is *Watchman*; the repo carries the AEGIS prefix. Changing either later is
again a repo-wide token replace plus `go mod edit -module` — but the first push
goes out under the final name.

### 2. Do the internal process docs ship? — ✅ RESOLVED (held out)

Three families of file exist only because of *how* the project was built, not
*what* it is:

- `BRIEF.md` — the internal handoff brief (authored for Rob; names the
  originating newsletter and personnel).
- `WORKLOG.md` — the working log (~4,100 lines of per-cycle narration).
- `PHASE0-DISCOVERY.md` / `PHASE0-COVERAGE.md` / `PHASE1-PLAN.md` — Phase 0/1
  working documents.

Resolution for the first public cut: **`BRIEF.md` and `WORKLOG.md` are held
out** — untracked and kept in the private workspace. They are process
narration, not product, and the brief names internal personnel. The Phase 0/1
documents **do ship**: the public `README.md`, `SECURITY.md`, CI, and code
comments reference them as the coverage matrix and build plan, so they are
part of the product's public rationale, not private pads. Nothing in the code
or operator docs depends on `BRIEF.md` / `WORKLOG.md`, so adding them back
later is a plain `git add` + push. (A `.gitattributes export-ignore` would not
have helped — GitHub publishes the tree, not `git archive`.)

### 3. Copyright holder — ✅ RESOLVED

`LICENSE` carries the standard Apache-2.0 text with the copyright line
**"Copyright 2026 CRPerdue Technologies, LLC"** (the company; the GitHub owner
is the personal account `crperdue73`). No separate `NOTICE` file is present;
none is required for a first-party project with no bundled third-party work.

## What this page is not

It is not a substitute for reading the diff before the first push. It is the
part of that reading a machine can do every time.
