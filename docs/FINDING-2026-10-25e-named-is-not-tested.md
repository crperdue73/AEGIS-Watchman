# FINDING 2026-10-25e — "named" is not "covered", and two rules may never fire

Beat 575. Two results, one lesson.

## 1. The covermap number, and why it is not good news

`scripts/covermap.py` classifies every *unread* pack value as **NAMED** (some file outside the pack
mentions it), **PACK-ONLY** (only the pack spells it — a spelling, not coverage), or **UNNAMED**
(nothing anywhere says it). Measured on the real tree:

```
unread values                  10
prior_event class names         3
NAMED somewhere in the tree    13
PACK-ONLY                       0
UNNAMED                         0  of 13
```

**0 unnamed, 0 pack-only looks like a clean report. It is not one.** The category I built to find the
gap came back empty, and the reason is in the category's own definition: `classify` counts a hit in
`WORKLOG.md`, `CHANGELOG.md`, `PHASE0-COVERAGE.md` and a dozen `docs/FINDING-*.md` as "named." Those
are documents *about* the rules. A rule value that appears in fifteen findings and zero test files
is the most-discussed-and-least-tested value in the project — and this tool calls it the safe class.

Worse: `covermap.py` is itself a file in the tree that spells the values it reports on. Two of the
"named in" lists were satisfied by the instrument.

So the honest reading of the covermap run is: **"of the 13 unread values, 13 are discussed somewhere.
Whether any of them is tested is a question this tool does not answer."** The number that would answer
it is a hit inside `*_test.go` / `test_*.py` only. That is a one-line change to `classify` and it is
NOT done in this beat — the tool as shipped answers a weaker question than its output implies, and
the output does not say so. That is the defect.

## 2. Two shipped rules that may never fire

`carbonato.container.host_root_mount` carries three globs, all tested against one string:

```yaml
detail_glob_any:
  binds:
    - "*:/host*"
    - "/:/"
    - "*/var/run/docker.sock*"
```

`internal/telemetry/docker.go:151` stores the binds as **one comma-joined value**:

```go
detail["binds"] = strings.Join(cfg.Binds, ",")
```

`anyGlobMatches` (`internal/rules/engine.go:237`) tests the whole string, its `path.Base`, and its
`pathSuffixes` — **never the comma-separated elements**. `matchOne` is segment-wise, so `*` does not
cross `/`. Transcribed and measured:

| binds value | `*:/host*` | `/:/` | `*/var/run/docker.sock*` |
|---|---|---|---|
| `"/:/host,/var/run/docker.sock:..."` | **False** | **False** | True |
| `"/:/"` (only bind) | False | **True** | False |
| `"/:/data"` | False | **False** | False |
| `"/:/etc"` | False | **False** | False |

- **`/:/` cannot match a host-root mount.** The only shape it matches is the literal string `/:/`,
  i.e. a bind with no destination at all. The real shape is `/:/<dest>`, and `matchOne` requires equal
  segment counts, so `/:/` (3 segments) never matches `/:/host` (4). Dead for its stated purpose.
- **`*:/host*` is almost dead.** It survives only when the *whole joined string* has no `/` after
  `:` except the ones in `/host` — e.g. a single bind whose source contains no `/`. Two binds, or one
  bind from a normal host path, and it's False.

⇒ A container started with `-v /:/host` **and nothing else** is caught. The same escape plus any
second bind mount (a docker socket, a data volume — the realistic case, and the exact case
`internal/rules/carbonato_pack_test.go:148` constructs) is caught **only** by the docker-socket glob.
Remove the socket bind and the critical alert goes silent.

The existing test cannot see this: `TestShippedCarbonatoFiresOnContainer` asserts the rule **id** is
in the fired set, with a binds value containing the socket. It passes on the socket glob alone. A test
that says "the rule fired" does not say *which glob* fired, so a rule with one live glob and two dead
ones passes it forever.

## 3. What I got wrong in this beat, kept because it's the same lesson

I wrote the covermap test to prove the classifier could say UNNAMED — the right instinct, since
"0 unnamed" is exactly the result to distrust. It took **five** corrections to get to `11 ok, 0 fail`:

1. the test ran the *project's* `covermap.py`, which derives its root from `__file__`, so every fixture
   case walked the real tree and returned the real project's answers;
2. table parse split on `"': "`, which never occurs — the row is `key: 'value'` with the quote at EOL;
3. `ROOT` double-derived, so `ROOT/scripts/covermap.py` pointed nowhere;
4. two substring cases built on pack values that *contain* `*` (`*golf-glob*`, `*alpha-glob*`) — a
   string with a `*` in it can never be a substring of real code, so those cases were impossible to
   pass; and
5. a duplicated `E` block, where my edits kept landing on the first copy while the second, stale one
   ran last and overwrote the result.

And the same `__file__` bug, fixed in one call site, was **still present** in case F — the case whose
entire job is to prove a clean result gets refused, and which was itself reading a pack that wasn't
empty.

Mutation-tested at the end: `allnamed`, `allunnamed`, `nooutside` — all three die by name against
`covermap_test.py`. The test can fail.

## 4. Next

- `classify` should distinguish *documented* from *tested*; report them as separate columns.
- Exclude `scripts/covermap*.py` from its own hit lists.
- The binds findings need the pack owner: either split `binds` into elements before matching, or
  store binds as a list. Both are behaviour changes to a critical rule and are not mine to make
  silently.
- A test per glob: assert each of the three globs fires **on its own**, not that the rule fired.

Pack untouched this beat: **117 / 39 / 78**.

---

# Addendum, beat 576 — the fix found a second instance in itself

`classify` now splits the hits into **tested / named-in-test / documented / self-only / pack-only /
unnamed** and the summary prints all of them. Three fixes, each one a place the tool was agreeing with
the project's prose:

- `TEST_RE` explicitly lists the shapes a test runner actually executes (`*_test.go`, `test_*.py`,
  `*_test.py`, `*.test.*`). Deliberately not "any `.py`" — a broad pattern re-creates the bug.
- `SELF_RE` excludes `scripts/covermap*.py`. The instrument was satisfying its own "named in" for two
  of the 13 (beat 575's output showed it).
- `tested_for(rid, hits)` requires a test file to **also name the rule** (by id or by its last dotted
  segment). Without this, `listen` came back TESTED on the strength of
  `internal/central/email_test.go:32` — `net.Listen("tcp", "127.0.0.1:0")`, a stub server. The string
  is in the file; the rule is not what that test is about. Same shape as "the rule fired" vs "the
  right glob fired."

**Result:** 10 values + 3 prior_event classes → **13 of 13 tested for their own rule, 0 named-in-test,
0 documented-only, 0 unnamed.**

That is a *better* number than beat 575 reported, which is the reason to distrust it — the same
direction-of-travel that made "0 unnamed" wrong an hour earlier. So each of the 13 was walked to its
first test hit by hand:

| value | rule | first test hit |
|---|---|---|
| `listen` | `net.listener_bound` | `internal/rules/carbonato_benign_test.go` |
| `privileged`, `host_pid`, `host_network` | `container.privileged_start` | `internal/central/fleet_test.go`, `engine_test.go` |
| `*:/host*`, `/:/`, `*/var/run/docker.sock*` | `container.host_root_mount` | `carbonato_pack_test.go` |
| `privileged`/`host_pid`/`host_network`, `identity.drift` | `container.after_identity_change` | `behavior_test.go` |
| `identity.drift` | `exfil.egress_after_identity_change` | `behavior_test.go` |
| `process.exec` | `secret.read_after_shell` | `internal/rules/coverage_test.go` |

They are real Go tests constructing real actions. **And the third row is the sting:**

- `carbonato_pack_test.go:156` contains exactly one line about this rule — the map entry
  `"carbonato.container.host_root_mount": true` — and that map is checked *after the engine ran*, so
  it asserts the rule id fired, not which glob.
- No test anywhere pins `"/:/"` in isolation: `grep -rn '"/:/"' --include=*_test.go` returns nothing.

So `/:/` — the glob beat 575 measured as able to match only the literal three-segment string `/:/`
and never `/:/host` — is certified as "tested for its own rule" by the one test that structurally
cannot see it. **The category is correct and the coverage is still hollow.** That gap is not
something `classify` can close by grepping harder; it needs a test per glob.

## Mutation-tested, both directions

- `if rid is not None and tested_for(...)` → `if in_tests:` — **dies**: case H goes red
  (`got 'tested', want 'named-in-test'`).
- `is_self()` → `return False` — **dies**: case I goes red (`got 'documented', want 'self-only'`).

`covermap_test.py`: **14 ok, 0 fail** (11 + cases G/H/I). Gate: **29 suites / 536 checks**.

Two fixture errors on the way, both mine, both instructive:
- cases `C` and `E` asserted `'named'` — a class that no longer exists, because the old expectation
  *was* the bug in assertion form;
- the first `I` fixture wrote the bare string `kilo-token` into the temp `covermap.py`, replacing the
  program under test. It died with `NameError: name 'kilo' is not defined` and the case returned
  `None`. A test that breaks the thing it measures measures nothing.

Pack untouched: **117 / 39 / 78**.
