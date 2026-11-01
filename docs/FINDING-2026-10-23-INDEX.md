# INDEX — the 2026-10-23 rule-pack investigation

Five documents, one afternoon of probing, and one of them takes down the one before it. Read in this
order; the order matters, because later findings correct earlier ones and nothing here has been patched.

| # | Document | Question asked | Outcome |
|---|---|---|---|
| 1 | `FINDING-2026-10-23-verb-not-slot.md` | Is `subject: command` the right restriction? | **No.** All four noise hits are object-slot. The obvious fix blinds three attacks. |
| 2 | `FINDING-2026-10-23-verb2.md` | Does the verb (argv[0] + subcommand) separate noise from attack? | **Yes** — 35 keys, 1 ambiguous. Self-corrects #1's pessimism. |
| 3 | `FINDING-2026-10-23-flagprobe.md` | Is the discriminator ever a FLAG (the ladder's step 2)? | **Once.** 2 flag-carried, 5 positional. Step 2 demoted to a single per-rule predicate. |
| 4 | `FINDING-2026-10-23-crossprobe.md` | Can a verb2 own a rule alone (is step 1 real)? | **Yes**, 18/18 pure — but the table is 18 populated keys, not 35. |
| 5 | `FINDING-2026-10-23-dead-globs.md` | Do the shipped globs fire at all? | **No.** `*/etc/cron*` and `*/.profile` patterns are dead: the rule looks armed and is not. |

## The ladder, as it now stands

1. **verb2 allowlist per rule** — measured, survives its own test (#2, #4). The only step with evidence.
2. **One flag predicate**, on `privileged_container` only (#3). Not a schema tier. Not a class.
3. **Session transitivity** — the real remaining gap. Does the object leave the machine? No slot, verb, or
   flag lever reaches it. Schema + engine change, **unscheduled**.
4. **Dead globs are a separate, older defect (#5)** and outrank all of the above in severity: a rule that
   can never fire is worse than a rule that fires too often. Not patched — no Go toolchain on this host.

## The rule that came out of the day

Every probe must print **its denominator and how many cases were capable of failing.** Three corrections
in one day; the first was a reasoning failure and the last two were the instrument scoring itself
generously — an empty key and a pure key printed in the same column.

## Status

Report-only. **Nothing in `rules/carbonato.yaml` or `internal/` has been changed.** The pack owner's call.
No commit manufactured: the toolchain is absent on this host (`go`, `make`, `gofmt`), so no gate could be
run and none was claimed.

## Addendum — two more findings, same day

- `FINDING-2026-10-23-globfix.md` — the matcher-widening idea is **measured and withdrawn** (token-boundary
  runs rescue 1 of 39 globs). Four specific glob spells proposed instead, each proved on the rule's own
  samples plus a no-new-quiet-line clause: 4/4. One of the four (`privileged_container`) is fixed for the
  sample, not the class.
- `FINDING-2026-10-23-neargo.md` — **the probe tool itself was looser than the engine.** `packcheck.py`'s
  needle fallback was missing the engine's "core has no '/'" check. Replaced by `scripts/neargo.py`,
  a source transcription with 13 pinned behaviours. **Every published number reproduced identically
  under the stricter port** (4 dead, 4/4 fixes, 1 of 39 rescued) — the defect was real and did not
  matter to the conclusions. Read this one before trusting any number in the others.
- `FINDING-2026-10-23-fieldcheck.md` — **the denominator I never printed.** `packcheck`'s "39 globs
  checked" is the `argv_any` slice only: 36% of the pack's 108 matched values. `path_glob_any` (30,
  four `secret.*`/`persist.*` rules), `endpoint_glob_any` (25, three `exfil.*`/`c2.*` rules) and
  `detail_equals` are read by **no local guard at all**, and `exe_any` by no sample-led one. Read this
  before quoting "4 dead of 39" — the honest sentence carries both numbers.
- `FINDING-2026-10-23-eventcheck.md` — wrote the missing samples for the blind keys (**7 rules, 36
  paths/endpoints**): all 7 LIVE. Two of MY samples were wrong and were moved to `UNSURE`, not deleted.
  And `fieldcheck.keys_by_rule` **drops the last list item** — the verdict was measured against a pack
  missing `*llamarpc.com*`. Claim is "all globs my parser saw". `eventcheck_test` asserts the bug is
  present so it cannot go quiet.
- **`FINDING-2026-10-23-retraction.md` — READ AFTER EVENTCHECK, NOT BEFORE.** The "7/7 live" headline is
  withdrawn: a second probe (`scripts/blindcheck.py`, 28 fixtures) over the same 7 rules finds **5 broken**,
  incl. `secret.read_model_key` matching nothing at `~/.config/openai/api_key` (critical), `persist.file_write`
  never consulting its globs on a rename, and `*/alchemy.com*` firing on `alchemy.com.evil.example`.
  Lesson: a sample set can have a full denominator and still be blind. eventcheck and blindcheck disagree;
  blindcheck is right.
- `FINDING-2026-10-23-typecheck.md` — **the wire check, all 20 rules at once.** Each rule's glob predicate read
  against the detail field its action type actually emits, both sides parsed from source with line numbers.
  **12 wires checked, 1 dead:** `persist.file_write` reads `detail['path']`, `FileWrite` never sets it on a
  rename (CREATE/DELETE only) — same defect blindcheck found behaviourally, confirmed structurally.
  Also corrects the retraction's emphasis: the three `secret.*` `file.access` rules have **no collector at all**
  (eBPF adapter is a documented `R2.2` stub) — that is the P2.0 gate, not a pack defect, and their globs are
  currently unreachable regardless of spelling. One wire (`host_root_mount`) this file cannot judge, and says so.
- `FINDING-2026-10-23-exeprobe.md` — **the first clean one.** 2 rules use `exe_any`, 13 terms, all bare names, 39
  reach checks, 0 layout-dependent. `exeMatches` is asymmetric (`bash` catches `/bin/bash`; `/bin/bash` catches only
  itself), so a path term silently pins a rule to one host — the pack doesn't do that, and `exeprobe_test` (9/9)
  fails the day someone does. Logged as a pass on purpose: five corrections in a row shouldn't train the next-me
  to invent a defect.
- `FINDING-2026-10-23-rename.md` — **READ THIS ONE FIRST IF YOU READ NOTHING ELSE.** Corrects my own last four
  hours: on a rename `detail["path"]` IS set (filewatch.go:66) and holds the **source** — so `persist.file_write`
  misses `mv x /etc/cron.d/evil` and fires on `mv /etc/cron.d/evil x`. Opposite of what I said in five places.
  `renametrace.py` prints the chain from source; `renametrace_test.py` 6/6 shows both directions against the pack's
  own globs. Fix lives in `internal/`, not `rules/`. Also records two of my own process failures (an overwritten
  script and a retraction I nearly published on a stale `ls`).
  → the older `typecheck` (wire) file is **superseded** and lives in `docs/superseded/`.
- `FINDING-2026-10-23-unlink.md` — the other direction of the same read. `persist.file_write` has **no
  nametype predicate**, so a delete of a cron entry and a write of one produce the identical alert: 10 globs,
  30 record shapes, 30/30 indistinguishable. Opposites for a persistence rule (persistence vs anti-forensics),
  and the obvious `detail_equals: {nametype}` patch would silence renames entirely — one decision, both ends,
  pack owner. Also records the fourth instrument-be-the-defect moment of the day (my sample generator stripped
  the glob's stars and tested the directory), pinned in `unlinkprobe_test`.
- `FINDING-2026-10-23-pathmatchdiff.md` — **the instrument audit.** 16 (glob, string) pairs against Go's documented
  `path.Match`: 14 agree, 2 disagree (character classes: Go negates with `!` and treats `^` as literal; neargo's
  regex leak does the opposite). **Impact 0 of 55 pack globs contain `[` or `!`** — latent trap, every finding
  520–527 stands. Records four wrong expectations of mine (I blamed neargo for my own sample errors) and the
  splice that deleted `substring_needle` from `neargo.py`. Open question left deliberately unanswered:
  `'*/.config/openai*'` does not reach `/home/x/.config/openai/api_key` under faithful path.Match — rule dead for
  the layout its title names, or engine suffix step not reproduced. **Start next session here.**
- `FINDING-2026-10-23-suffix.md` — **closes the 4:00 PM open question, against the rule not the port.**
  `'*/.config/openai*'` is three slash-pieces and needs the string's first segment to be `.config`; every
  `.config/` suffix of `/home/x/.config/openai/api_key` is three segments. 7 globs × 12 paths = 84 pairs,
  canonical key path reached by **0**. What it hits instead: `/home/x/.config/openai` — **the directory, not the
  key file inside it** (second "aimed one level off" glob of the day, after `*/etc/cron*`). 3 of 12 credential
  layouts reached. Ordering for the pack owner: R2.2 eBPF sensor first (524), then the glob, and only then the
  untestable assumption — whether `detail["path"]` carries a file or a directory watch. Two more of my own wrong
  assertions recorded; the printed table corrected me, third hour running.
- `FINDING-2026-10-23-segmentaudit.md` — **the pattern question, answered "mostly no".** 55 globs, 7 of 20 rules;
  **29 are needles (no '/')** and out of scope, so 26 path globs are surveyed: 9 WILD-FILE, 13 UNCLEAR (honest
  unknowns), 1 FILE-OK, **3 DIR-SHAPED = 12%**, of which one is 529's. **The segment-aim defect is two globs, both
  already documented — no hidden swarm.** `*/.kube/config*` labelled DIR-SHAPED but its wildcard tail still reaches
  `~/.kube/config`; recorded as "watch", not a finding. Classifier's first draft invented 40 problems; second draft
  wrongly called `*credentials*` DIR-SHAPED (it's a needle and reaches `/root/.aws/credentials`). Fourth hour running
  where the printed table, not my intent, corrected me.
- `HANDOVER-2026-10-23.md` — **the week's findings assembled for the pack owner, ordered by what blocks what:**
  (1) `secret.read_model_key` never executes (eBPF stub, R2.2 — Dad's call) — outranks every glob finding below it;
  (2) rename reports the SOURCE; (3) `persist.file_write` is nametype-blind, and the obvious patch would silence
  renames (one joint decision); (4) exactly two segment-aim globs, extent signed off by survey; (5) two untested
  assumptions named as such; (6) nothing touched in `rules/`/`internal/`, no commit; (7) how to run every guard
  without Go, plus the day's four self-inflicted instrument defects kept in the record.
- `FINDING-2026-10-23-predaudit.md` — `scripts/predaudit.py` + test (28 checks). Does any rule predicate on a
  `detail_*` key its action family's handler never sets? **0 of 9 fields offending — no.** The denominator is the
  finding: **20 rules, 5 carry any detail predicate, 15 carry none**, so this instrument speaks to a quarter of the
  pack and says so out loud instead of reporting "clean". Confirms the handover's §1 and narrows §2–§4. Four
  instrument defects caught pre-publication, the worst being a regex that could never match `const X = "..."` — which
  left `net.listen` out of the vocabulary, skipped its rule, and still exited 0. A coverage hole wearing a green
  light, in my own tool.
- `FINDING-2026-10-23-binds.md` — `scripts/bindprobe.py` + test (21 checks). `carbonato.container.host_root_mount`
  is a critical rule globbing `detail["binds"]`, which `docker.go:145` joins with a comma. The engine matches the
  **whole joined string**, and `substringNeedle` refuses any glob containing `/` — so the contains-fallback is off
  for exactly the globs that need it. **The rule fires only when the escape mount is the LAST bind.** `/:/host` and
  `...,/:/host` fire; `/:/host,/data:/data` does not. 7 of 9 cases; the 2 misses are one shape: escape mount first.
  A silent miss on a critical rule for the rule's own stated CARBONATO vector — sits directly under the sensor gap
  in the handover. Fix is a decision (producer split recommended over glob spelling), not a patch from me.
  Probe's draft 1 was wrong first: the quote-strip bug, same as 532's, two hours apart.
- `FINDING-2026-10-23-drift.md` — `scripts/driftprobe.py` + test (51 checks). The three `prior_event` rules are
  wired correctly, end to end: drift emits on change/add/remove, `ObserveDrift` feeds the timeline, and
  `cmd/collector/main.go:144` runs it **before** `Poll()` so the file half lands before the action half is judged.
  **Clean pass, logged as a pass.** Two bounds named for the pack owner: the default timeline window is **15m** and
  `container.after_identity_change` asks for **10m** — two thirds of it, so shrinking `NewTimelineSized` silently
  kills a critical rule (invariant worth a startup check); and `cap=256` is per-host, so a chatty `process.exec`
  stream can evict the exec `read_after_shell` is waiting for. Probe's first red was itself (checked `process.exec`
  against `schema.go`; it is declared in `telemetry.go`). Negative tests remove each gate and confirm the flip.


## 2026-10-24

- `FINDING-2026-10-24-clock.md` — `scripts/clockprobe.py` + test (47 checks). **`Action` has no time field** —
  `Type` + `Detail`, nothing else. So `identity.drift` enters the timeline under a per-event `ev.TS`
  (`ObserveEvent`) while `net.egress`/`process.exec` enter under the poll clock (`asOf := e.now()`). Cost 1: a
  correlation resolves only as finely as its **coarser** half, so 0.001s drift precision against a 30s action
  clock spends **four orders of magnitude for nothing**. Cost 2: `Seen`'s `!o.At.After(asOf)` means a drift
  stamped *ahead* of its own poll is invisible — but that needs a clock stepping **backwards** (NTP slew, VM
  resume), so it is a clock-discipline dependency, not routine. Not a cross-host skew finding; both clocks are the
  same host's. Not a proposed `internal/` change: `ts` on `Action` is an additive public-schema decision, yours.
  **Three assertions in my first draft passed unconditionally (`ok(..., True)`)** — the exact clean-0/0 trap, in my
  own file, the same night I was quoting it. Replaced with a modelled `seen()` truth table. And a **fifth**
  span-based reader bug: `expr(src, "type Action struct")` swept into `Rule`/`Actor`/`NewEnvelope`, so the
  field-count check read eleven names; replaced with brace-aware `struct_block()`.

## 2026-10-24 (cont.)

- `FINDING-2026-10-24-severity.md` — `scripts/severityprobe2.py` + test (46 checks). **A CLEAN PASS, and a
  deliberate one.** `engine.go:74` copies `ev.Severity = r.Severity` with no check — after six findings of that
  exact shape, this looked like the seventh. It is not. `Rule.Validate()` rejects empty AND unknown at load
  (exactly one call site), and `severityRank`'s `default: return 0` ranks unknown values **lowest** with a comment
  saying that is intentional, "so an unrecognized value never masks a known high." With EITHER guard present a
  `Critical`-for-`critical` typo is harmless; it takes removing both. Pack: 20 rules, 6 critical / 7 high / 7
  medium, all valid, no quote contamination. **Because a PASS is weaker than a FAIL unless it can be falsified,
  the test asserts each guard exists in source AND removes each guard to confirm the predicate flips.** Tally
  note for the reviewer: the defects cluster on the **runtime correlation path** (timing, ordering, ring capacity,
  producer gating); the **static authoring path** (rule shape, severity, glob syntax at load) is validated and
  sound. Reader bug caught by the test: `case "info", "low", ...` is ONE multi-value case and my regex read only
  the first quoted string — same family as the week's recurring one-value-several-spellings bug, this time on the
  Go side.

## 2026-10-24 (cont.)

- `FINDING-2026-10-24-pathdiff.md` — `scripts/pathdiff.py` + test (45 checks). **Where it came from:** 539's pass
  ("severity is validated at load") raised the next question — does EVERY entry into the system go through
  `ParsePack`? Yes: collector->LoadPacks, cmd/rules->loadSet, pins->PinFile, all funnel through `ParsePack`. That
  thread is genuinely closed. **But pins.go is a SECOND implementation of the path traversal**, and a comment
  claims the two match. Measured: **file selection genuinely IS shared** (`packFileNames` defined once, both call
  it; the suffix filter exists in exactly one place) so the comment is TRUE as written. **The path-list walk is
  copy-pasted, and the copies disagree on one input:** `loadSet` rejects a set contributing zero rules with a
  comment saying why ("verified: 0 rules is a green light for nothing"); `PinSet` has no such guard, so
  `watchman-rules pin /typo'd/dir` writes a 0-pin lockfile and exits 0. **Deliberately ranked BOTTOM:** an empty
  pin set cannot approve real packs (`verify(0,n)` fails for all n>0, modelled n=1..9); it is a misleading
  artefact, not a bypass. **The test carries a retirement condition** - it detects if PinSet ever gains the guard
  and prints a notice to re-verify and retire the finding, because a finding that cannot announce its own
  obsolescence becomes folklore. Reader bug mine: searched the comment as one string when the source wraps it
  across lines 76-77 - normalise the carrier before matching the content (same lesson as the YAML list marker).
