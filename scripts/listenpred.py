#!/usr/bin/env python3
"""listenpred — a presence-test predicate on a key the emitter always sets filters nothing.

WHY THIS EXISTS (beat 718)
--------------------------
`carbonato.net.listener_bound` matches `type: net.listen` + `detail_non_empty: ["listen"]`.

`net.listen` actions come from exactly one function, `AuditRecord.ListenAction()` in
internal/telemetry/egress.go. That function has two paths: return NO action when the address is empty, or return an
action whose `"listen"` key holds that non-empty address. There is no third path.

So `detail_non_empty: ["listen"]` can never reject anything, and the rule is equivalent to `type: net.listen` alone.
The match block *looks* like a filter and is not one.

This script re-derives that from source rather than restating it, so the claim can be re-run after the emitter changes.

WHAT IT CHECKS
--------------
1. Locate `ListenAction()` and confirm every `return schema.Action{...}` that yields an action also sets `"listen"`.
2. Find every rule using `detail_non_empty` / `detail_non_empty_any`, and for each key ask: does the emitter set this
   key unconditionally? If yes -> the predicate is a tautology.
3. Report each rule as `ok` (predicate does work) or `offender` (predicate cannot be false).

Keys known to be conditional are listed in CONDITIONAL with the reason, so a *new* unconditional-key predicate fails
loudly rather than being waved through.

The listener_bound offender is DECLARED and pinned: fixing it is a rules edit whose intent is the pack owner's call
(see docs/FINDING-2026-10-31b-listener-bound-predicate-is-a-tautology.md). A different or additional offender fails.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PACK = ROOT / "rules" / "carbonato.yaml"
EGRESS = ROOT / "internal" / "telemetry" / "egress.go"
DOCKER = ROOT / "internal" / "telemetry" / "docker.go"

# Keys whose presence is CONDITIONAL at the emitter, with where that is established. A rule may use these as a
# real filter. Anything not listed here must be proven unconditional before a presence test on it is accepted.
CONDITIONAL = {
    "privileged": "internal/telemetry/docker.go sets it only under `if cfg.Privileged`",
    "host_pid": "internal/telemetry/docker.go sets it only under `if cfg.HostPID`",
    "host_network": "internal/telemetry/docker.go sets it only under `if cfg.HostNetwork`",
}

DECLARED = {"carbonato.net.listener_bound"}


def rules(text):
    for part in re.split(r"\n  - id: ", text)[1:]:
        rid = part.split("\n")[0].strip()
        m = re.search(r"match:\n((?:      .*\n)+)", part)
        yield rid, (m.group(1) if m else "")


def presence_keys(match_block):
    """Collect keys used by detail_non_empty / detail_non_empty_any, list-style."""
    out = []
    for field in ("detail_non_empty", "detail_non_empty_any"):
        m = re.search(rf"{field}:\n((?:        .*\n)+)", match_block)
        if m:
            for ln in m.group(1).splitlines():
                k = ln.strip().lstrip("- ").strip().strip('"')
                if k:
                    out.append((field, k))
    return out


def listen_action_sets_listen():
    """Return (ok, evidence). ok means: every action ListenAction can return carries a non-empty 'listen'."""
    if not EGRESS.exists():
        return False, f"missing {EGRESS}"
    src = EGRESS.read_text()
    i = src.find("func (r *AuditRecord) ListenAction()")
    if i < 0:
        return False, "ListenAction() not found in egress.go"
    body = src[i:]
    body = body[: body.find("\n}\n") + 3]
    # Path 1: refuse to emit an action when the address is unusable.
    guards = re.search(r'if\s+!\s*ok\s*\|\|\s*addr\s*==\s*""\s*\{[^}]*return\s+schema\.Action\{\}[^}]*false', body)
    # Path 2: the emitted action's detail map sets "listen" to that address.
    sets = re.search(r'["\']listen["\']\s*:\s*addr', body)
    # Count returns that actually hand back an action. The guard returns `schema.Action{}` (the zero value) with
    # ok=false, so matching bare `schema.Action{` counts it too and falsely reports two action paths -- the
    # first version of this check did exactly that and went green for the wrong reason (beat 718).
    emitted = re.findall(r"return\s+schema\.Action\{[^}]*\}\s*,[^,]*,\s*true", body)
    if not guards:
        return False, "ListenAction() no longer short-circuits on an empty address — re-derive by hand"
    if not sets:
        return False, "ListenAction() no longer sets 'listen' from addr — the tautology may be gone (good: re-pin)"
    if len(emitted) != 1:
        return False, f"ListenAction() now has {len(emitted)} action-return paths; this check assumes one"
    return True, "'listen' is set on the only action path; the empty-address path returns no action"


def main():
    if not PACK.exists():
        print(f"FAIL  no pack at {PACK}")
        return 1

    taut, evidence = listen_action_sets_listen()
    listener_is_tautology = taut
    print(f"listenpred: {evidence}")
    if not taut:
        print("  NOTE: the emitter changed. The tautology claim may no longer hold — re-derive before trusting")
        print("  the finding doc.")

    offenders = []
    total = 0
    for rid, block in rules(PACK.read_text()):
        keys = presence_keys(block)
        if not keys:
            continue
        total += 1
        bad = []
        for field, k in keys:
            if k == "listen" and listener_is_tautology:
                bad.append(f"{field}:{k}")
            elif k not in CONDITIONAL and k != "listen":
                bad.append(f"{field}:{k}")
        if bad:
            offenders.append((rid, bad))
            print(f"  offender {rid}: presence test cannot be false ({', '.join(bad)})")
        else:
            print(f"  ok {rid} (presence test rejects something)")

    if total == 0:
        print("FAIL  no rule uses a presence-test predicate — this check would be vacuous")
        return 1

    if not offenders:
        print(f"\n  all {total} presence-test rules narrow for real")
        if DECLARED:
            print(f"  STALE DECLARATION: {sorted(DECLARED)} no longer offends — remove it from DECLARED.")
            return 1
        return 0

    if {r for r, _ in offenders} == DECLARED:
        print(f"\n  DECLARED: {len(offenders)} rule's only predicate is a tautology (pinned, awaiting a pack-owner")
        print("  decision). A different or additional offender fails this check.")
        return 0

    print(f"\n  {len(offenders)} rule(s) use a presence test that cannot be false, and the set is not the")
    print("  declared one. Either make the predicate discriminate or remove it and own the broad match.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
