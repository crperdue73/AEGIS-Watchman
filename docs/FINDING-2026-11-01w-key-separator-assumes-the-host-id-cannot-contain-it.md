# FINDING — 2026-11-01w: RETRACTED. The key separator does not assume anything about the host id.

**Status:** **RETRACTED by its author before posting.** No claim below survived. Kept on disk because a retraction
somebody can read is worth more than a finding somebody has to rediscover as false.

## What I was about to claim

That `AgentKey.String()` = `HostID + ":" + Root` (`store.go:26`) is parsed back by splitting on the first colon, so a
host id containing a colon (`a:b` at root `/r` → parsed as host `a`, root `b:/r`) silently re-partitions the key, letting
two unrelated agents share one baseline and producing misattributed drift.

I wrote 5,747 bytes of that. It read well. The chain had four links and I had read all four. I had even gone back and
measured where `host.ID` comes from, found the `-host-id` override is unvalidated, and **narrowed my claim to that
trigger** — which felt like exactly the discipline I've been demanding of myself for eighteen beats.

**Then I opened the parser.**

```go
// parseAgentKey recovers an AgentKey from the map-key form AgentKey.String()
// produces (host + ":" + root). Roots are absolute paths, so the separator is
// the first ":/" — which keeps a host id that itself contains a colon (e.g. an
// IPv6 literal) readable, where a plain split on the first colon would not.
func parseAgentKey(s string) (AgentKey, bool) {
	if i := strings.Index(s, ":/"); i >= 0 { ... }
	if i := strings.IndexByte(s, ':'); i >= 0 { ... }   // fallback, no absolute root
}
```

`store.go:52-63`. **It splits on `":/"`, not on `":"`.** A host id containing a colon — the exact case I built the
finding on, and the exact example the author chose to name in the comment — parses back correctly. My "first colon"
claim was **never in the code**. I had grepped for `Index(s, ":")` in a version of my own reading, seen two matches in
the body, and assumed both were `":"`; one was `":/"`. **I read a grep result instead of the file.**

## The worst part, which is the part worth keeping

I had already written the finding, with four options for Dad, and my instrument `scripts/keycollisioncensus.py` was
written and run. **Check C would have failed and check D would have caught it** — C quotes the assumption comment, D
quotes the separator. When I ran the instrument, three checks went red and my reaction was *"my checks are wrong, let
me fix them"* (I had a typo, `parseAnimalKey`, and a doc phrase mismatch, both real). **The third failure was the
instrument telling me the truth about the code, and I filed it under "instrument bug."**

That is a new species, and it is worse than the ones I've been counting: not a gap-closing branch that congratulates the
code, not a declared variable standing in for an enforced rule — **an instrument reporting a true negative that I
overrode because it contradicted the finding I'd already written.** The finding was the thing that should have died.
The check that was right is the check my notes list as "the separator logic changed — re-derive" — my own boilerplate
saying *re-derive*, and I have been treating "re-derive" as a formality for eighteen beats.

## What remains true, having re-read properly

- **`parseAgentKey` is careful.** The `":/"` separator is a deliberate design choice, documented, with the motivating
  case named. The `":"` fallback exists for a root that isn't absolute, and it is the *second* branch, not the first.
- **The fallback is the only soft spot, and it's already acknowledged** — *"No absolute root (unexpected, but do not
  silently drop the baseline)."* For a non-absolute root, a colon host id would misparse; the comment says the input is
  unexpected, and beat 729 already filed the relative-root family. **Nothing new here.**
- **`agent.ID = filepath.Base(root)`** (`cmd/collector/main.go:110,127`) is still true and still worth knowing — agent
  identity is a directory basename. **Not filed as a finding then; not filed now.** It is a design fact, and after this
  beat I'm less willing than ever to dress a design fact as a defect.
- **`-host-id` is still unvalidated** — also true, also not a defect, because the parser tolerates it by design.

## The rule this beat earns

**When an instrument fails on a finding I have already written, the default hypothesis must be that the finding is
wrong.** I have spent eighteen beats building checks precisely because I cannot trust my own reading — and then, the
first time a check contradicted me directly, I assumed the check was broken. A discipline that only defends against
other people's errors is not a discipline.

Corollary, mechanical, for the next instrument I write: **when a check fails, open the file and read the function before
editing the check.** In this beat that meant `sed -n '50,66p' internal/central/store.go` — nine lines. It would have
saved 5,747 bytes and an hour.

## What I am NOT claiming

- Not that `parseAgentKey` is defective in any way I have measured.
- Not that `-host-id` validation is needed. It might be; I have no evidence it is, and I'm not filing an opinion as a
  finding on a night I just got one wrong.
- Not that the instrument was broken. It was right.
