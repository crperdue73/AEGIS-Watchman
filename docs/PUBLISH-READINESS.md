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
| Module path / project name | ⏳ **placeholder** — see below |
| Internal process docs in the public tree | ⏳ **decision** — see below |
| Copyright holder / `NOTICE` | ⏳ **decision** — see below |

## Open decisions for Rob

### 1. The name

The module path is the placeholder `github.com/CRPerdue/agentids` (from
`go.mod`). The brief (`BRIEF.md`) leaves the product name to Rob — suggested
"WATCHMAN" or "WARDEN". The name is baked into, at minimum:

- `go.mod` module path, and every import that begins `github.com/CRPerdue/agentids/`
- `Makefile` (`PKG := github.com/CRPerdue/agentids/internal/buildinfo`)
- `deploy/docker/Dockerfile` image labels (`.../agentids`)
- `cmd/*` binary names and the docs that use them

A rename is a mechanical find/replace **plus** a `go mod edit -module` (and any
import rewrite). It is cheapest to do **once**, before first push. Decide the
name first.

### 2. Do the internal process docs ship?

Three families of file exist only because of *how* the project was built, not
*what* it is:

- `BRIEF.md` — the internal handoff brief (authored for Rob; names the
  originating newsletter and personnel).
- `WORKLOG.md` — the working log (~3,500 lines of per-cycle narration).
- `PHASE0-DISCOVERY.md` / `PHASE0-COVERAGE.md` / `PHASE1-PLAN.md` — Phase 0/1
  working documents (owner line names the individual).

Some open-source projects publish exactly this as design rationale; many do not.
It is a positioning call, not a correctness one. If they are **not** to ship,
they must be removed from the published repo (a `.gitattributes export-ignore`
does **not** help — GitHub publishes the tree, not `git archive`). The code and
operator docs do not depend on any of them.

### 3. Copyright holder

`LICENSE` carries the standard Apache-2.0 text. Confirm the copyright line /
holder name that should appear, and whether a `NOTICE` file is wanted.

## What this page is not

It is not a substitute for reading the diff before the first push. It is the
part of that reading a machine can do every time.
