# Publish readiness

The plan (`PHASE1-PLAN.md`, `README.md`) is that this repository is **public under
Apache-2.0 from the first cut**. It was developed inside a private workspace, so
"publish-ready" is its own target, separate from "feature-complete." This page is
the checklist for that target: what is verified, and what is a decision — not a
code change — that only Rob can make.

The machine-checkable half is a script, not a habit:
`scripts/publish-scan.sh` (also `make publish-scan`, and a CI job named
`publish`). It scans the **tracked tree** *and* the **commits a push would
publish**, and fails on three classes:

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

### History is part of what ships (and was a real blind spot)

The first version of this scan grepped the tracked tree only, on the reasoning
that "only tracked content ships." That is false: `git push` publishes
**commits**, and history is permanent. A value committed once and later removed
still ships, and a file that was `git rm`-ed is fully retrievable from any clone
(`raw.githubusercontent.com/<owner>/<repo>/<sha>/<path>` serves it). So the scan
now covers both:

- `make publish-scan` (the gate, and the CI `publish` job): the tracked tree +
  the commits not yet on the upstream — the ones a push would *newly* publish,
  still fixable locally. Fatal on any hit.
- `make publish-audit`: the same classes over **every commit reachable from
  HEAD** — what a clone sees. These are reported, not fatal: by the time anyone
  reads them they are already public on a pushed branch, and a local fix cannot
  un-publish them. Remediating them (rewrite the history *before* it is public,
  or accept the exposure) is a human decision, not a build failure.

That was not hypothetical. The audit on this repo finds the operator home path
`/home/<user>` in six files across the public history — `cmd/manifest/main.go`,
`examples/identity.drift.json`, three test files, and `WORKLOG.md` — even though
the tracked tree at HEAD is clean (the fixtures were fixed; the fix cannot reach
back into earlier commits). It also shows the full `WORKLOG.md`/`BRIEF.md` are
still retrievable from pre-hold-out commits (see below). No credential,
RFC1918 address, or private service port appears in history.

The checklist below is partly machine-checkable, so one command runs it all:
`make preflight` (`scripts/preflight.sh`) executes every CI job's local
of-record — build, lint (incl. `pin-check`), `go vet -tags sqlite`, tests
(default and sqlite), demo, quickstart, container, publish-scan, the release-cut
self-check, deps, fuzz — and
stops at the first failure. It ends with a **fresh-clone check**
(`scripts/clone-check.sh`): CI runs on a clean checkout, so the local verdict
clones `HEAD` and builds, tests, and demos *there*, where a file present but
untracked cannot hide. "Push-ready" is that command's exit status, not
eight separate recollections of what passed. (It needs Docker and module
downloads, the same as CI.)

That "same as CI" is itself checked: `make ci-parity-check` (wired into
`make lint`, so both CI and a local preflight run it) reads `.github/workflows/
ci.yml` and fails unless every command a CI job runs is reachable from
`preflight` — a `make T` must be a target preflight invokes or one it depends
on; any other command must appear in preflight, a Makefile recipe, or a script
preflight calls. A CI job that gains a step fails the local gate until the local
gate gains it too, instead of the two drifting until a red first push.

### The gate has a control, and it is not run by the gate

`scripts/suitecheck.sh` runs every `scripts/*_test.{py,sh}` and requires each to
hold. It **excludes `suitecheck_test.sh`**, because that file copies the tree and
runs the gate — including it would recurse forever.

That exclusion is correct and it has a cost worth stating plainly: the harness
which validates the gate is the one thing the gate structurally cannot run.
**From about beat 653 to beat 706 it was RED and nothing said so.** Case M8 is
the control — it runs the gate once, unmutated, and requires the real suite and
check counts — and it sat pinned to `65 suites / 1445 checks` while the tree had
grown to `96 / 1772`. Every log line in that stretch read "gate green." Nothing
was wrong with the gate. What rotted was the only instrument that would have
noticed the gate changing shape, because it only ran when someone remembered.

So: `make gate-selfcheck` runs it, deliberately, and it is slow (~7 min — it
copies the tree twice and runs the full gate inside the copy). Run it when the
product's suite count changes, before a release cut, or whenever you want to
know the gate's alarms still fire. It is **not** in `preflight` on purpose — a
7-minute self-copy in the push path would get skipped, and a skipped check is
the failure this page exists to prevent.

Two rules learned the hard way here, both worth more than the mechanism:
- **A control that depends on someone remembering to run it is a control that
  rots.** If M8's verdict matters, it needs a scheduled reader, not a habit.
  (Still open — it wants a cron/nightly home, and that is a box-level change.)
- **When a copy of the tree is tested, any path derived from `__file__` silently
  changes meaning.** `windowblind.py` resolved its workspace to `/` inside the
  copy and failed for a reason unrelated to what it checks. Suites that need the
  real workspace take `VIVI_WS`, which must be the WORKSPACE (the directory
  containing `memory/`), not the project root — those are three levels apart,
  and passing the wrong one produces the same failure, which reads like the fix
  did not apply.

## Checklist

| Item | Status |
|---|---|
| `LICENSE` (Apache-2.0) | ✅ present |
| `README.md` (overview, build, run, known blind spots) | ✅ |
| `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md` | ✅ |
| CI: build, vet, test (default + sqlite), lint, demo, quickstart | ✅ |
| CI: container image built and checked (`make container-check`) | ✅ |
| CI: publish scan (`make publish-scan`) | ✅ |
| No credentials in the tracked tree **or in unpushed commits** | ✅ scan clean |
| No operator home path in the tracked tree / unpushed commits | ✅ scan clean |
| No private infrastructure in the tracked tree / unpushed commits | ✅ scan clean |
| Already-public history audit (`make publish-audit`) | ⚠️ `/home/<user>` fixtures in 6 files; `BRIEF.md`/`WORKLOG.md` blobs — see §2 and above |
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

### 2. Do the internal process docs ship? — ✅ RESOLVED (held out going forward)

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

**⚠️ Correction (found by `make publish-audit`): the hold-out is forward-only;
the content is already in the public history.** `BRIEF.md` and `WORKLOG.md`
were tracked for most of the build and were removed from the tree only at the
hold-out commit `ea6348d` ("hold internal docs out"), which is itself on
`origin/main`. Git never forgets: both files are retrievable from any clone
(`raw.githubusercontent.com/crperdue73/AEGIS-Watchman/2f209f6/WORKLOG.md`
returns the full log; `BRIEF.md` likewise). "Held out" therefore means "out of
the tree from `ea6348d` forward," not "never published" — they have been public
since the first push. Correcting that is a decision for Rob: leave it (the
content is engineering narration and the brief's personnel references, not
secrets or credentials) or rewrite history to drop those blobs (a force-push of
public history, which this project's own tag policy otherwise forbids). The
scan cannot make that call, and neither can a build.

### 3. Copyright holder — ✅ RESOLVED

`LICENSE` carries the standard Apache-2.0 text with the copyright line
**"Copyright 2026 CRPerdue Technologies, LLC"** (the company; the GitHub owner
is the personal account `crperdue73`). No separate `NOTICE` file is present;
none is required for a first-party project with no bundled third-party work.

## Cutting a release

The preconditions are `make preflight` exit 0 and a push credential with write
access to `crperdue73/AEGIS-Watchman` — a fine-grained PAT scoped to this repo
with **Contents: write**, or an SSH deploy key with write access. Then, rather
than typing the cut by hand, run the script — it does exactly the three steps
below and **refuses the three ways to get them wrong** (a dirty tracked tree, a
version that already exists locally or on the remote, a branch other than
`main`):

```sh
scripts/cut-release.sh 0.2.0          # dry run — prints the plan, changes nothing
scripts/cut-release.sh --go 0.2.0     # preflight, push main, tag, push tag
```

Underneath, what `--go` runs, in order:

```sh
git push origin main                  # publish the commits
git tag -a <version> -m "watchman <version>"
git push origin <version>             # the release is the tag
```

The tag is created on the tip that was just pushed, so the tag and the pushed
commit are the same bytes by construction — there is no window where they can
disagree.

The script is itself checked: `make cut-release-check` runs a clean cut **and
fires every refusal** end to end in a throwaway clone (`scripts/cut-release-check.sh`),
so a broken refusal cannot wait until a real cut to show itself. `make preflight`
runs this check, and so does the CI `publish` job — on a **full-depth** checkout,
because the check clones the checkout and pushes a scratch branch, and git
refuses to push a shallow branch (a CI-only requirement a full local clone cannot
reveal). So a broken refusal shows up on a push, not at a cut on the public repo.

**A published tag is never moved.** `0.1.0` is already public — it is the tree at
the first push. Re-pointing a released tag would break every clone, mirror, and
pin that already resolved it, which is exactly the failure this project is built
to catch. Work since a cut ships under a **new** version, with its own tag and
its own `CHANGELOG.md` section. If a tag should not have gone out, the honest
fix is to leave it (or delete it while nothing has consumed it) and cut the next
version — never to rewrite it.

## What this page is not

It is not a substitute for reading the diff before the first push. It is the
part of that reading a machine can do every time.
