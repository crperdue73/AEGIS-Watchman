# FINDING — 2026-10-31: the pack cannot see a credentialed URL in argv, and that is how 247 files leaked

**Status:** measured, **not fixable with the current rule language**. Needs an engine capability, not another glob.
**Instrument:** no instrument is published for this finding (the audit script is local-only).
**Severity of the blind spot:** high — this is the single largest observed secret-leak vector on this fleet.
**Raised by:** beat 711's fleet measurement; written up beat 712.

## What was measured (not hypothesised)

a local audit script (unpublished), run against the real workspace, found the vault push credential
(`http://user:pass@host`) in **251 files**. Classifying the context around each occurrence:

| context | files |
|---|---|
| inside a JSON `"command"` field — i.e. **executed as argv** | **247 (98%)** |
| documentation prose (a notes line quoting the URL) | 18 |
| other | ~4 |

Re-written in the last 24 hours: **55 files** (3 in the last hour, jade 48 of them). The count moved
249 → 251 in ~40 minutes during the observation. **The leak is live, and it is one habit repeated by 15 agents:
inlining a credentialed URL into `git`/`curl` instead of sourcing `$VIVI_VAULT_PUSH`.**

A secret in argv is not caught by file-based rules, because the value never touches a watched path — it is
typed into a command line, and the transcript (or the process table, or the shell history) is where it lands.

## The rule language cannot express it

The pack's `argv_any` family is glob-only, and glob matching here is **hierarchical**: `*` and `?` do not
cross `/` (`path.Match` semantics, `internal/rules/engine.go:231`). Measured through `packcheck.match_one`
(the transcription of the engine's `anyArgvMatches`):

```
"*://*@*"  vs  "https://user:pass@vault.example/x.git"   -> False
"*://*@*"  vs  "git clone https://u:p@h/r.git"           -> False
"*://*@*"  vs  "git clone https://github.com/r.git"      -> False   (correct)
"*://*@*"  vs  "curl -s http://198.51.100.7/x"            -> False   (correct)
```

**The obvious glob fails on the obvious true positive.** `://` then `user:pass@` are separated by an arbitrary
number of characters *containing slashes*, and no glob can say "any characters, slashes included".

Two globs happen to fire, but only via `substringNeedle`'s contains-fallback, and both are unusable as a rule:

```
"*https://*"  -> True     # fires on every https URL, credentialed or not: zero specificity
"*@*"         -> True     # fires on every e-mail address, every decorator, every `user@host`: noise
"*:*@*"       -> False    # contains-fallback refuses a core with no wildcards... but here the core has
                          # wildcards, so path.Match applies and `*` cannot cross ':' rules either
```

This is the **third** time the same structural limit has produced a finding (beat 644: endpoints need the
contains-fallback; beat 649: the argv family was never fixtured; this beat: a family that cannot be
expressed at all). The pattern is consistent — **the pack's matching language is a path matcher, and a growing
share of the threats are not paths.**

## What a real fix requires (Dad's call — engine change, not a rules edit)

A **credential-shape matcher** — a new rule type, roughly:

```yaml
match:
  type: process.exec
  argv_credential_url: true     # or: argv_regex: "[a-z][a-z0-9+.-]*://[^/@\\s]+@"
```

Properties it must have, each of which the existing fallback fails:
1. **Scheme-anchored** — `scheme://` before the `@`, so `user@host` and `e-mail` don't fire.
2. **Non-empty userinfo** — something must precede the `@` inside the authority.
3. **A password separator inside the userinfo** — the actual signal is `user:pass@`, not merely `user@`.
4. **Bounded** — must not backtrack across a whole command line (the pack runs on every exec).

Note this is deliberately a *detector*, not a *redactor*: it says "a credential went past in argv", which is the
Carbonato-class signal (an agent doing something with a secret it had no constraint to do). Redaction is a
different component and a different decision.

## Why I am not shipping this today

- It needs a **Go toolchain**, absent on this host since 10-08 (box-wide, Dad's call). A rule I cannot run is a
  claim, not a check — beat 636's rule, applied to myself.
- It changes the **rule schema**, which is a product decision with a compatibility surface: every pack in the
  wild parses against it.
- The alternative — approximating it with `*https://*` and accepting universal false positives on every URL an
  agent touches — would make the rule's alert stream worthless, and I would rather file a hole than ship noise
  that trains operators to ignore the rule.

## What can be done *without* an engine change, and what it costs

`substringNeedle` refusing slashes was retracted in beat 644 for exactly this reason (endpoints need the
fallback). But here the fallback is what *allows* the only two usable globs, and neither is specific enough.
So: **no rules-only fix exists.** The options are the engine capability above, or accepting that argv-borne
credentials are outside Watchman's current detection surface — stated in the docs rather than left implied.

**Interim mitigation that needs no code:** make inlining impossible rather than detectable — a `git-remote` /
helper wrapper that emits only `$VAR`. That is a fleet practice change, already put to Dad in beat 711.
