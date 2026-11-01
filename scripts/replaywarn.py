#!/usr/bin/env python3
"""replaywarn — the restart-replay default is silent, and it compounds with the poll-clock defect.

WHY THIS EXISTS (beat 721)
--------------------------
`cursorPath(cfg)` (collector/behavior.go:295) returns `cfg.Behavior.Cursor`, else `<spool>.cursor`, else "". An empty
path means `SetCursor` is never called and `saveCursor` is a no-op, so the audit-log read offset is never persisted.
On restart the collector re-reads the log from byte 0 and re-fires every rule the retained lines still match.

That is documented three ways -- in the FileSource type comment, in the config table, and in the deploy doc -- and it is
still the ZERO-CONFIG BEHAVIOUR, because `spool` has no default. Nothing warns.

Standalone it is duplicate-alert noise. Combined with the poll-clock defect (beat 720, `asOf := e.now()` per batch) it
becomes a correctness failure: the replay is not only duplicated, it is stamped at replay time, so a twelve-hour-old
exec can satisfy a `within: 2m` correlation. Two findings multiplied.

WHAT THIS CHECKS
----------------
1. `cursorPath` still falls back to "" (so the silent default still exists).
2. `SetCursor` is the only thing that sets the cursor, and it is called only when the path is non-empty.
3. The spool has no default in the config docs (i.e. the zero-config user hits it).
4. The config table still documents the *mechanism* without the *consequence*.

Each clause is independently falsifiable. If the default is made safe (a warning, a default path), the relevant clause
stops holding and this check tells me to retire or narrow the finding rather than keep quoting it.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BEH = ROOT / "internal" / "collector" / "behavior.go"
ENG = ROOT / "internal" / "telemetry" / "engine.go"
CFGDOC = ROOT / "docs" / "collector-config.md"


def read(p):
    if not p.exists():
        print(f"FAIL  missing {p}")
        sys.exit(1)
    return p.read_text()


def fn_body(src, sig):
    i = src.find(sig)
    if i < 0:
        return None
    j = src.find("\n}\n", i)
    return src[i : j + 3] if j > 0 else src[i:]


def cursor_falls_back_to_empty(beh):
    body = fn_body(beh, "func cursorPath(")
    if body is None:
        return False, "cursorPath() not found"
    # Match the terminal `return ""` specifically. A bare endswith-check on the trimmed body is wrong: the body ends
    # with `}` (beat 721 caught this -- the first version reported "no empty fallback" on source that plainly has one).
    if re.search(r'return\s+""', body):
        if "log.Printf" in body or "warn" in body.lower():
            return False, "cursorPath() now warns or defaults -- the silent default may be fixed"
        return True, 'cursorPath() falls back to `return ""` with no warning: the silent default stands'
    return False, "cursorPath() has no empty fallback -- re-derive before repeating the finding"


def setcursor_only_path(beh):
    body = fn_body(beh, "func newBehavior(") or beh
    call = re.search(r'if\s+cur\s*:=\s*cursorPath\(cfg\);\s*cur\s*!=\s*""\s*\{[^}]*SetCursor\(cur\)', body)
    if call:
        return True, 'SetCursor is called only when cursorPath != "": empty path persists nothing'
    if "SetCursor" in beh:
        return False, "SetCursor's call shape changed -- re-derive"
    return False, "SetCursor not called from the collector at all -- the finding has moved"


def spool_has_no_default(cfgdoc):
    # The config table's spool row: a default column value of "—" means no default, so zero-config hits the fallback.
    for line in cfgdoc.splitlines():
        if line.lstrip().startswith("| `spool`"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[1] in ("—", "-", ""):
                return True, "`spool` has no default in the config table: zero-config users get the replay path"
            return False, f"`spool` now has a default ({cells[1]}) -- zero-config may no longer replay"
    return False, "no `spool` row found in collector-config.md -- the table changed"


def consequence_undocumented(cfgdoc):
    # The row names the mechanism ("else not persisted") but not what it costs.\n    # CAREFUL: the row already contains the word "replaying" in "so a restart resumes instead of replaying the log" --
    # that is the BENEFIT of persisting, not the COST of not persisting. Grepping for the word `replay` here reported
    # the consequence as documented when the very opposite was true. That is beat 716's mistake exactly: a substring
    # match is not a reader. Look for a cost phrase, not a keyword.
    COST = ("duplicate", "re-fire", "re-fire", "noisy", "alerts again", "replayed at", "missed")
    for line in cfgdoc.splitlines():
        if line.lstrip().startswith("| `cursor`"):
            low = line.lower()
            if any(c in low for c in COST):
                return False, "the cursor row now names the consequence -- the doc half of the finding is stale"
            if "not persisted" in low:
                return True, 'the cursor row says "else not persisted" but never what a replay costs the operator'
            return False, "the cursor row changed shape -- re-read it"
    return False, "no `cursor` row found in collector-config.md"


def main():
    beh, eng, cfgdoc = read(BEH), read(ENG), read(CFGDOC)

    checks = [
        ("silent empty default", cursor_falls_back_to_empty(beh)),
        ("SetCursor gated on non-empty", setcursor_only_path(beh)),
        ("spool has no default", spool_has_no_default(cfgdoc)),
        ("consequence undocumented", consequence_undocumented(cfgdoc)),
    ]

    all_hold = True
    for name, (holds, evidence) in checks:
        if holds:
            print(f"  ok {name}: {evidence}")
        else:
            print(f"  changed {name}: {evidence}")
        all_hold = all_hold and holds

    # The compounding claim: the replay is also mis-timed. That rests on engine.go's batch clock, which eventclock.py
    # owns. Here we only assert that the dependency is still present, so this finding is never repeated without it.
    batch_clock = bool(re.search(r"asOf\s*:=\s*e\.now\(\)", fn_body(eng, "func (e *Engine) Matches()") or ""))
    if batch_clock:
        print("  ok compounding: batch clock still present (engine.go:85), so a replay is also mis-timed")
    else:
        print("  changed compounding: batch clock gone -- beat 720 may be fixed; re-check before reusing this")

    if all_hold and batch_clock:
        print("\n  DECLARED DEFECT (live, unchanged): restart replay is silent, and it compounds with the poll clock.")
        print("  See docs/FINDING-2026-11-01b-restart-replay-default-is-silent.md. Re-derived each run, not muted.")
        print('  Cheapest fix: one startup log line when cursorPath(cfg) == "" and audit_log is set.')
        return 0

    print("\n  The finding's shape changed. Re-derive it before repeating it -- do NOT keep quoting the old claim.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
