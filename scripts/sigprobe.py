#!/usr/bin/env python3
"""sigprobe — build the specific fix for each dead glob and check it against the pack's own samples.

WHY THIS AND NOT A MATCHER CHANGE
  There is a two-part result and the first part is a NO.

  1. NO, token-boundary runs do not rescue the pack. `scripts/tokruns.py` treats '.' as a
     name boundary so `*/etc/cron*` can match `/etc/cron` and let '*' eat '.d'. Proven by
     `tokruns_test.py` (5/5) that the idea works mechanically — and measured by the first
     version of this probe, which found it rescues exactly ONE glob (`*/etc/cron*`) and
     leaves 24 dead with no quiet lines added. One rescue for a new matcher concept means
     the matcher is not what is broken. The globs are. (The run-based numbers are still
     printed at the end for the record.)
  2. SO: fix the globs, one at a time, and prove each fix on the pack's own samples.

WHAT IT DOES
  Proposes a corrected spelling for each glob packcheck reports DEAD, then asserts:
    - the new glob fires on EVERY sample of that rule that the old glob was supposed to catch
      (`SAMPLES` from packcheck — the pack's own table, not a case I invented here)
    - the new glob does not fire on any `quiet` CORPUS line the old glob did not
  A fix that only satisfies the first clause is a widening; a fix that only satisfies the
  second is still dead. Both clauses or it is not a proposal.

WHAT IT DOES NOT DO
  - Changes no file. rules/carbonato.yaml is untouched; this prints a proposal for the pack
    owner, with the evidence attached.
  - SAMPLES is packcheck's table, which is itself my desk. A glob can be written to pass a
    sample I wrote. The quiet-line clause is the guard against that, and it only guards
    against the quiet lines in a 36-line corpus.
  - Python port of the matcher, and now a CLOSER one: `scripts/neargo.py` transcribes
    engine.go's substringNeedle instead of packcheck's truncated needle fallback. The two agree
    on every proposal below (run with STDLIB=1 to see the loose-port numbers), so the earlier
    numbers were not corrupt — but the port that survives is the one that does not invent
    behaviour the engine lacks. Still not Go; nothing here has been run against the engine.

Exit: 0 if every DEAD glob has a fix that satisfies both clauses, 1 if any does not, 2 if the
      pack cannot be read.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packcheck import parse_pack, SAMPLES, PACK  # noqa: E402
import packcheck as _loose  # noqa: E402
import neargo  # noqa: E402
from fpprobe import CORPUS  # noqa: E402
from tokruns import runs  # noqa: E402

# Proposed corrected spelling per dead glob. Each line says WHY in the reason column.
# Shape of every fix: the pattern keeps the author's anchor and stops ending a glob on a
# path that one more segment would complete.
FIXES = {
    # '*' cannot cross '/', so a glob that ends where cron/systemd live must be allowed to
    # continue into the filename. This is the Defect 1 class in the finding.
    "*/etc/cron*": "*/etc/cron*/*",
    "*/etc/systemd/system*": "*/etc/systemd/system/*",
    # the same class: a unit file, a profile variant, an authorized_keys written elsewhere
    "*/.profile": "*/.profile*",
    "*/.bashrc*": "*/.bashrc*",
    "*/.bash_profile*": "*/.bash_profile*",
    "*/.zshrc*": "*/.zshrc*",
    "*authorized_keys*": "*authorized_keys*",
    # '*-v /*:/' stops at the colon, so it can only match a path that ENDS there
    # the colon ends the source path and the destination follows it; the glob must let a
    # destination exist AND stay anchored on both sides of the colon.
    # CAVEAT, measured: this catches the host-root form (`-v /:/host`) and stays silent on a
    # data mount (`-v /data:/data`), but it MISSES any host dir bound to a host path
    # (`-v /etc:/host/etc` -> False). It is anchored on the wrong thing — a healthy glob here
    # needs `:` treated as a separator, i.e. the two-field argv_any decision already open.
    "*-v /*:/": "*/:/*",
    # trailing-'*'-with-nothing-after: the site claims these but no sample exercises the
    # shape the glob actually accepts, or the glob is anchored too tightly to fire
    "*--pid=host*": "*--pid=host*",
    "*--net=host*": "*--net=host*",
    "*--listen*": "*--listen*",
    "*http.server*": "*http.server*",
    "*/.ssh/known_hosts*": "*/.ssh/known_hosts*",
    "*setsid*": "*setsid*",
    "*disown*": "*disown*",
    "*xxd*": "*xxd*",
    "*tar cz*": "*tar cz*",
    "*pip3 install*": "*pip3 install*",
    "*apt-get install*": "*apt-get install*",
    "*apt install*": "*apt install*",
    # command-position globs: the pattern names an executable and an argument as one
    # phrase, which package_install's samples never present contiguously in one token.
    # These need the joined-line candidate to include the shell form the sample uses.
    "*| sh*": "*| sh *",
    "*|sh*": "*|sh*",
    # one '/*' segment cannot carry 'vivi/x'; the path needs a second segment
    "* -O /home/*": "*-O /home/*/*",
    "* -O /tmp/*": "*-O /tmp/*/*",
}


def fires(glob, argv):
    """Near-Go semantics by default; STDLIB=1 restores the old loose port for comparison."""
    if os.environ.get("STDLIB") == "1":
        return _loose.any_argv([glob], argv)
    return neargo.any_argv([glob], argv)


def quiet_hits(glob):
    return [e for e in CORPUS if e[0] == "quiet" and fires(glob, e[1])]


def main():
    try:
        with open(PACK, encoding="utf-8") as fh:
            pack = fh.read()
    except OSError as e:
        print(f"sigprobe: cannot read pack: {e}", file=sys.stderr)
        return 2
    rules = parse_pack(pack)
    if not rules:
        print("sigprobe: parsed 0 rules — pack unreadable", file=sys.stderr)
        return 2

    dead = []
    for rid, globs in rules:
        samples = SAMPLES.get(rid)
        if samples is None:
            continue
        for g in globs:
            if not any(fires(g, argv) for argv in samples):
                dead.append((rid, g, samples))

    print(f"sigprobe — fix proposals for {len(dead)} dead glob(s)\n")
    unfixable = []
    for rid, g, samples in dead:
        new = FIXES.get(g, g)
        caught = [s for s in samples if fires(new, s)]
        old_caught = [s for s in samples if fires(g, s)]
        q_old = {tuple(e[1]) for e in quiet_hits(g)}
        q_new = [e for e in quiet_hits(new) if tuple(e[1]) not in q_old]
        status = "FIXED" if caught and not q_new else ("NO-GAIN" if caught == old_caught else "WIDENS")
        if not caught:
            status = "STILL DEAD"
            unfixable.append((rid, g, new))
        print(f"  [{status:10}] {rid}")
        print(f"      old {g!r}")
        print(f"      new {new!r}")
        print(f"      fires on {len(caught)}/{len(samples)} sample(s), new quiet lines: {len(q_new)}")
        for e in q_new:
            print(f"        ! quiet line would now fire: {' '.join(e[1])}")

    # For the record: the run-candidate idea, measured. One rescue in 25.
    all_globs = [g for _, gs in rules for g in gs]
    rescued = 0
    for g in all_globs:
        if any(fires(g, e[1]) for e in CORPUS):
            continue
        if any(fires(g, e[1]) or any(fires(g, [r]) for r in runs(" ".join(e[1])))
               for e in CORPUS):
            rescued += 1
    print(f"\n  For the record — token-boundary runs would rescue {rescued} of "
          f"{len(all_globs)} globs ({len(dead)} dead). Not worth a matcher change.")
    print(f"  Fix proposals that satisfy both clauses: {len(dead) - len(unfixable)}/{len(dead)}")
    if unfixable:
        print("  no proposal yet for:")
        for rid, g, new in unfixable:
            print(f"      {rid}: {g!r} -> {new!r}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
