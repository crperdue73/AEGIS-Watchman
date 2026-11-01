# FINDING — 2026-11-01c: the scope capability key keeps the port, so a CDN change looks like a new capability

**Status:** measured, **not fixed** — the fix is a one-line key derivation, but it changes what a shipped rule fires
on, which is a product decision, not a bug fix. **Dad's call.**
**Severity:** medium — false positives on a **high**-severity rule, on shipped defaults, with no knob.
**Instrument:** `scripts/scopekey.py` (re-derives the key the way `capabilityOf` does, then asks whether changing it
would alter any current verdict).
**Written up:** beat 722. This is the audit beat 721 promised: *"`scope.seen` has the identical empty-string fallback —
I have not audited what a non-persisted scope-seen costs."*

## Part 1 — the answer to beat 721's open question (and it is worse than the cursor)

Beat 721 found the audit cursor silently doesn't persist without `spool`/`cursor`. **`scope.seen` has the same
empty-string fallback (`behavior.go:309-316`), and its cost is higher.**

Without a persisted seen-set, the scope shim's memory is per-process, so **every restart re-emits every capability the
agent routinely uses as `novel: true`** — and `carbonato.tool.scope_new` is **severity: high**, matching
`type: scope.change` + `detail_equals: {novel: "true"}` (`rules/carbonato.yaml:17-35`).

The source says this itself, in `config.go:72-74` and `scope.go:47-53`:

> *"a collector restarts on every deploy and reboot, and a restart that re-emits every capability the agent routinely
> uses is exactly the kind of false burst that gets a rule tuned off."*

**So the consequence is understood and written down by whoever wrote it — and still unguarded.** A high-severity rule
fires in bulk on every deploy for the zero-config user. The author's own stated fear ("gets a rule tuned off") is what
the default produces. That is not a bug in the shim; it is a missing warning, identical in shape to beat 721.

**What I verified and can now say is sound** (so this does not become another vague suspicion):
- `saveSeenLocked` is **atomic** (temp + fsync + chmod 0600 + rename), no-ops when the set is empty, and is called
  **before** the derived events are emitted — so a crash cannot lose a key that was already reported.
- The **corrupt-file path is genuinely bounded**: `PersistSeen` returns an error, the caller logs it, the shim runs
  in-memory and overwrites on the next new capability. **One-time duplicate, never a startup failure.** Documented and
  honest.
- The **cap (8192) is real** and evicts oldest-first; a capability untouched long enough may re-fire once, which
  `scope.go:79-81` calls "a bounded, self-healing duplicate, never a standing alert." Accurate.

## Part 2 — the new defect: the capability key contains the port

`capabilityOf` (`scope.go:332-355`) derives the key for `net.egress`:

```go
// Prefer the named service (stable, DNS-free) over the raw IP:port, so
// the capability key is "slack.com" rather than a rotating IP that would
// look novel every time the CDN changes.
if v, ok := a.Detail["service"].(string); ok && v != "" { return ScopeKindEndpoint, v }
if v, ok := a.Detail["endpoint"].(string); ok && v != "" {
    // endpoint may be "service ip:port"; take the first token as the key
    // so identity survives the IP suffix.
    return ScopeKindEndpoint, strings.Fields(v)[0]
}
```

**The comment states the goal — "identity survives the IP suffix" — and the code cannot achieve it.** `strings.Fields`
splits on **whitespace**. It is reaching for the *first space-separated token* of `"service ip:port"`, which is the
service name. But that branch is only reached when `service` is **absent**, and when `service` is absent `endpoint` is
the bare `"host:port"` from `decodeSockAddr` (`egress.go:150,155`). **There is no whitespace in it.** So `Fields(v)[0]`
returns the entire `host:port`.

Measured (transcribed from the source, then confirmed against the emitter):

| endpoint detail | capability key |
|---|---|
| `93.184.216.34:443` | `93.184.216.34:443` |
| `93.184.216.34:8443` | `93.184.216.34:8443` |
| `93.184.216.34:4444` | `93.184.216.34:4444` |
| `slack.com 1.2.3.4:443` (+ `service: slack.com`) | `slack.com` ← named path, correct |

**So the key is `ip:port`, port included, for every peer that isn't in the short hardcoded `knownServices` list.**
`knownServices` is explicitly "a minimum viable set… representative entries, not exhaustive"
(`egress.go:247-249`, `:273-276`). **The common case is the unnamed case.**

**Consequences, both directions:**
- **False positive:** the same host on a different port is a **different capability**. A CDN edge serving on 443 and
  8443, a dev server that moves, an app that retries on 4444 — each is `novel: true` → `carbonato.tool.scope_new`
  (high) fires. **This is precisely the CDN-rotation false positive the comment says it is preventing.**
- **False negative (rarer but real):** if a host is ever reached on the *same* port from a genuinely new context, the
  key collides with the old one and the novelty is swallowed.

**The intent and the code disagree, and the code is the shipped one.**

## What is NOT established

- **That the port should be stripped.** Stripping it makes `host` the key (arguably right); keeping it makes
  `host:port` the key (defensibly "a new port IS a new capability"). **I am not making that call** — it changes what a
  high-severity rule fires on for every existing user. It is a product decision.
- **Whether the raw-IP key is itself acceptable.** Even with the port stripped, an unnamed host is still keyed by raw
  IP, so CDN rotation **still** creates novel keys. The comment's stated goal is only half-achievable without a DNS/
  reverse-lookup step this product deliberately doesn't have. **The honest options are: strip the port (narrow the
  false positive), or document that unnamed peers are IP-keyed and will occasionally look novel.** I have not measured
  how often — that needs a real host's egress.
- **That this has caused a tuned-off rule in practice.** Cannot measure without a deployment.

## Recommendation

1. **Pick the key, then make the comment true.** If the key is the host, use `net.SplitHostPort` (already used in
   `serviceForAddr`, same file, same shape) instead of `strings.Fields` — the tool is in the building. If the key is
   `host:port`, **delete the comment's claim**, because it currently promises a stability the code doesn't provide.
   **Either way, the comment and the code stop disagreeing.** That is the part I'd fix regardless of which key wins.
2. **Beat 721's fix applies here unchanged and is now more urgent:** the `scope.seen` empty path re-fires a
   **high**-severity rule on every restart for zero-config users. One startup warning covers both knobs.
3. **Do not change the key on a heartbeat** (beat 714's rule). Measured, written up, handed over.

## Method note

Beat 721 ended with a named open question and an explicit *"I am not claiming it is harmless."* Tonight answered it:
the persistence half is **sound** (atomic, fsync'd, save-before-emit, bounded corrupt-file handling) and the *key*
half is defective. **I would have reported the wrong thing if I had assumed the suspicion was correct** — the answer
was "the part you asked about is fine, and the part next to it isn't," which is the third time this week that following
a named gap landed somewhere better than the gap.
