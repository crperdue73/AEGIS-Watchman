# FINDING 2026-10-23 — the wire check: one dead wire, one gate I mistook for a defect, one question I cannot answer

**Disposition:** Open, two items routed. `persist.file_write` reads a detail key the write path does not always set; three `file.access` rules are inert without the P2.0 adapter, which is Dad's decision, not a patch.
Fourth probe of the day over the same territory, and the first one that asks a **structural**
question instead of a behavioural one:

> For each of the 20 rules — which `detail` field do its glob predicates read, and does the
> dispatcher for the action type it declares actually emit that field?

Both sides read statically at run time: `internal/rules/engine.go` (which key each predicate family
reads, with line numbers) and `internal/telemetry/` (which keys each action type emits). Nothing is
recalled; every line of output cites a file and line a reader can open.

## Result: 12 wires checked, 1 dead

```
[MISSING-FIELD] carbonato.persist.file_write (file.write):
    path_glob_any -> detail['path'] (engine.go:130)
    but FileWrite emits ['exe', 'nametype', 'pid', 'watch']
```

**This confirms `blindcheck`'s second finding by a different route** — behaviour there, static
wiring here — and it also sharpens it. `WriteAction()` fills `detail["path"]` only for
CREATE/DELETE records. On a `rename`/`renameat`/`renameat2` (all in `strictWriteSyscalls`) the
record is a write, `nametype` is whatever auditd sent, and `path` is never set. So the rule fires
every other predicate and then fails on a field that could not have been there. **The globs are not
wrong; they are never reached on the rename path.**

The rest: 9 wires OK (2 `net.egress` → `endpoint`, 6 `process.exec` → `argv`/`exe`, 1 more
endpoint), 3 rules with no field predicate (prior_event only), 3 `file.access` rules with no
collector — see below.

## One thing I reported wrongly three hours ago, and am correcting

Three rules depend on `file.access`: `secret.read_order_follows_constraint`,
`secret.read_after_shell`, `secret.read_model_key`. My first run reported **NO-COLLECTOR** for all
three, which reads like a pack defect. It is not one:

- `internal/telemetry/telemetry.go:44` declares the constant with the comment *"emitted by the eBPF
  adapter; declared here so the shape is documented even where the source is not yet built"*.
- `internal/telemetry/ebpf_linux.go` (build tag `ebpf`) returns
  `telemetry: ebpf source not yet implemented (R2.2)`.
- `internal/collector/behavior.go:182` logs "eBPF file.access adapter unavailable, running
  auditd-only" and degrades.

**These rules have no collector because the collector is the P2.0 toolchain gate** — the same
missing `clang`/`libbpf-dev` that has blocked eBPF since the migration. So: the three most
important rules in a Carbonato pack (secret reads, including the model key) cannot fire at all in
this build, not because the pack is wrong but because the sensor does not exist yet. That is worth
saying out loud, and it is Dad's call to re-provision, not mine to work around.

Consequence for the previous finding: my retraction blamed `secret.read_model_key`'s **glob** for
missing `~/.config/openai/api_key`. The glob does miss it — but in the current build the rule never
runs at all. Both are true; the bigger one is the missing sensor. A fix that stops at the glob would
be treating a symptom of an unbuilt adapter.

## The question this file cannot answer, stated as such

`carbonato.container.host_root_mount` uses `detail_glob_any`, which the engine reads from a map in
a `range` loop. My keyed-access extractor cannot follow that, so the check reports it as
`<key from rule>` and **does not pass it** — it is neither OK nor dead in this report. That is a
gap in my instrument, not a verdict on the rule. Counting it honestly: **12 wires checked of 13
wired predicates; 1 unanswerable; 12 capable of failing.** A probe that silently called it OK would
have been the fifth instrument today to report confidently about something it was not reading.

## Honest limits

- Static wiring only. `persist.file_write` is *reported* dead on rename because the write path sets
  `nametype`-dependent keys; if some future adapter fills `path` on renames, this check will still
  say MISSING-FIELD until the extractor learns more than `detail["x"] = `. It is a floor.
- `exe_any` is checked as a field wire but not as a matcher (`exeMatches` vs `anyArgvMatches`); the
  matcher semantics remain `neargo`'s business.
- No Go. Everything here is reading Go *source*, which is legitimate — a static wire check does not
  need the compiler — but nothing has been run, so the behavioural half is still near-Go.
- The extractor finds 7 of the pack's 8 action types; `net.listen` and `container.start` rules
  carry no field predicate, so their absence costs nothing today and would cost a false alarm
  tomorrow. Named, not fixed.

## Recommended next step

Route this to the pack owner as **one line: `persist.file_write` reads a detail key that the write
path does not always set; gate the rule on `nametype != DELETE` or teach `WriteAction` to emit the
destination on rename.** And separately, Dad: the three `file.access` rules are inert in this build
without the P2.0 adapter — that is a decision, not a patch.

Vivi 🌺 — 2026-10-23, ~11:45 AM ET
