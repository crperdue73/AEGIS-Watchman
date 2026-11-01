# Recovering this repository

If you are reading this because the working tree exists but `.git` does not — a bad
migration, a wiped volume, a clone that lost its object store — this is the procedure.
It was written from the real thing: a workspace migration on 2026-10-08 that preserved
every work file but dropped the entire local commit graph.

The rule for all of it: **do not fabricate history.** The public remote is the only
authority on what actually shipped. Reconcile *to* it; never guess commits into
existence.

## 1. Establish the truth from the remote

The shipped baseline is whatever the remote says right now — not what a stale memory
file claims. Pin it before you touch anything:

```sh
git ls-remote <remote>                       # main + tag refs, authoritative
git clone <remote> /tmp/recon                 # a clean copy to diff against
```

Record the exact `main` SHA and every tag SHA. In a recovery, "34 commits ahead" is a
memory, and memories lie after a migration. The SHA is evidence.

## 2. Diff the working tree against the baseline

Overlay the local tree on the fresh clone and let **git** decide what changed — not a
raw `find`. This matters: a `find`-based count includes **gitignored** files (runtime
state, local denylists, captured logs) that must never be replayed into public history.
`git status` honors `.gitignore`; a file listing does not.

```sh
cd /tmp/recon
cp -a <recovered-tree>/. .
git status --porcelain        # the real reconciliation set: ?? new, " M" modified
```

Two counts fall out, and only these two are the replay set:

- **`??` untracked, not ignored** — genuinely new files that belong in history.
- **`" M"` modified** — tracked files whose content changed vs. the baseline.

Anything gitignored is deliberately local runtime state. Leave it local.

## 3. Verify the recovered tree before you replay it

A recovery you have not tested is not a recovery. On a scratch extract of the tree,
run the guards that do **not** require a git object store:

```sh
scripts/publish-scan.sh     # no tracked file / unpushed commit carries a credential,
                            # operator home path, or private infrastructure
scripts/license-check.sh    # LICENSE is canonical Apache-2.0 (copyright filled)
make test                   # the suite itself
```

If those pass on the recovered tree, the *content* is sound. Know which guards are
git-dependent and will fail on a tree with no history — `cut-release-check.sh` and the
`@{upstream}..HEAD` half of `publish-scan`/`publish-scan-check` shell out to git and
cannot run until step 1–2 give you a repo again. That failure is expected, not a
regression; do not "fix" it by faking a clone.

Also scan the recovered tree for secrets before it goes anywhere:

```sh
grep -rIl '<any known token/credential pattern>' <tree>   # expect: no matches
```

## 4. Reconcile — the two honest choices

Both preserve truth; pick one and write down which. **This decision is the owner's**
(CRPerdue), because it changes public history.

- **Replay** — reconstruct commits from the `??`/`" M"` set on top of the baseline, then
  push. Preserves a readable history, but the *original* commit boundaries and messages
  are gone with the object store, so the replay is a reconstruction, and it should say
  so.
- **Squashed reconciliation** — one commit that brings the tree to its current state on
  top of the baseline. Honest, minimal, unambiguous; loses granularity.

Never re-point an existing public tag (e.g. `0.1.0`). Clones, mirrors and pins depend on
it. The next release gets a **new** version via `scripts/cut-release.sh --go <version>`
(dry-run by default — it prints the plan and changes nothing without `--go`).

## 5. Whatever you choose, back up first

Before any step that mutates history, snapshot the tree so the work cannot be lost a
second time, and keep the snapshot **outside** the repo (a tarball plus a delta manifest
is enough; exclude gitignored runtime state). The snapshot, not the memory of what was
in `.git`, is what makes the recovery repeatable.
