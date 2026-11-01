# FINDING — 2026-11-01j: the baseline key's encoding mangles relative roots, and the roster drops what it can't read

**Status:** measured from source + executable port. **Gap stands. NOT APPLIED** — every option is a behaviour or
format change, so it is Dad's call.
**Instrument:** `scripts/baselinekey.py`.
**One line:** `AgentKey.String()` glues `host + ":" + root` and the decoder splits on `:/` — for a root that isn't
absolute there is no `:/`, so it splits inside the host id instead.

## The code

```go
// internal/central/store.go:26
func (k AgentKey) String() string { return k.HostID + ":" + k.Root }

// internal/central/store.go:56
func parseAgentKey(s string) (AgentKey, bool) {
    if i := strings.Index(s, ":/"); i >= 0 {            // absolute root: correct
        return AgentKey{HostID: s[:i], Root: s[i+1:]}, true
    }
    if i := strings.IndexByte(s, ':'); i >= 0 {         // fallback: first colon anywhere
        return AgentKey{HostID: s[:i], Root: s[i+1:]}, true
    }
    return AgentKey{}, false
}
```

The comment above it reasons carefully about a **host id containing a colon** (IPv6 literal) and is **right for that
case** — an absolute root still contains `:/`, so the primary branch finds the real boundary.

The reasoning stops at the delimiter and never asks whether the delimiter is **guaranteed to exist**. It isn't: a
root that is not absolute has no `:/`, so the fallback fires and splits at the first colon — which is *inside the
host id*.

## Is this reachable? Asked before writing it up, and the answer is yes twice

**Roots can be relative.** `internal/collector/run.go:23` does `filepath.Glob(ar.Path)` and stores the match
**verbatim** as `Collected.Root`. `config.validate()` (`config.go:122`) requires only that `path` be **non-empty**.
`filepath.IsAbs` appears **0 times** in non-test code across `internal/` and `cmd/` (grep, measured).

**Host ids can contain colons.** `-host-id` is a free string (`main.go:29`), and the code's own comment treats an
IPv6 literal as a real case — so I am not inventing the premise.

## What actually goes wrong (run, not remembered)

`scripts/baselinekey.py` contains a faithful port of `parseAgentKey` and drives it over four cases. Measured:

| host | root | wire key | decodes to |
|---|---|---|---|
| `web-01` | `/home/u/.openclaw` | `web-01:/home/...` | correct |
| `::1` | `/home/x` | `::1:/home/x` | correct |
| `::1` | `ws-a` | `::1:ws-a` | **`{"", "::1:ws-a"}`** |
| `::1` | `ws-b` | `::1:ws-b` | **`{"", "::1:ws-b"}`** |

Two **distinct** agents decode to `HostID: ""`, each carrying the hostname glued onto the path it reports.

## What that damages, precisely

The map keys are still correct strings, so **storage does not merge the two agents.** I want that said plainly
because the dramatic version — "two agents collide into one baseline" — is false.

The damage is on the read path. `ListBaselines` (`store.go:103`) is the **only** consumer of `parseAgentKey`, and it
feeds the fleet identity roster, `GET /api/v1/agents` — the thing `BRIEF.md` calls the differentiator. For these
agents the roster prints **host `""`** with the hostname embedded in the path column. The identity surface reports an
identity belonging to no host.

Worse, the unparseable branch is `if !ok { continue }` — **silent.** An agent that cannot be parsed is an agent that
is running, collecting, and detecting, and is **absent from the roster** with no error, no log line, and no count in
the response. A missing row is indistinguishable from a host that was never enrolled.

## Why it's worth recording when the default config is safe

Today's shipped `collector.yaml` uses absolute paths with globs, so the **default deployment does not hit it.** I'm not
inflating that.

Three things make it real anyway:

1. **`config.validate()` is one `IsAbs` call away from preventing this entirely** and doesn't make it — and the
   failure it permits has *no error surface at all*.
2. **The author thought about the hard case and got it right.** The comment handles colon-bearing hosts. What it
   doesn't do is check that its own assumption about the data (the root is absolute) holds. **Same shape as beats
   724–728, one layer down: not an unexamined noun this time — an unexamined assumption about the input.**
3. **The two storage backends disagree about what an agent is.** The SQLite backend (beat 725) keys by columns and
   never calls `parseAgentKey`, so it structurally cannot lose identity this way. The memory/File backends can. The
   `Store` interface exists precisely so that backend choice doesn't change meaning; here it does.

## Options — NOT applied, not picked

1. **Reject a non-absolute root** in `config.validate()`. One line, fails at startup, loud. I think most correct —
   but it forbids a config someone may be running today, so it's a behaviour change.
2. **Change the wire form** to something unambiguous (JSON, or length-prefixed). Correct, but it changes
   `FileStore`'s on-disk format, i.e. a migration.
3. **Stop decoding entirely** — keep `AgentKey` beside the value (`map[AgentKey]`), so `ListBaselines` doesn't parse
   anything. The SQLite backend already proves the shape works. My favourite on the merits.
4. **Harden the parser to search for a suffix `:/`.** **I'd argue against this** — it makes an ambiguous encoding
   cleverer instead of less ambiguous, and in `"::1:ws-a"` there is no `:/` to find, so it fixes nothing here.

## What I am NOT claiming

- **Not** that two agents' baselines merge. They don't; the keys stay distinct.
- **Not** that the default config is affected. It isn't.
- **The measured claim is narrower than the dramatic one**, and that is deliberate: no baselines merge, the default
  config is safe, and the real damage is a roster row with the wrong identity plus a silent drop. I would rather ship
  the narrow true version than the alarming one that a careful reader could refute.
- **Not** Go behaviour. Source reads plus a port I wrote; no toolchain (beat 642).
- **Not** that option 1 is right. "Refuse to start" versus "accept and normalise" is a deployment-philosophy call,
  and picking one silently on a heartbeat is how a tool ends up with a policy nobody chose.
