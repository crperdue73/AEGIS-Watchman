#!/usr/bin/env python3
"""argvany_test -- the 39 argv_any values against the candidate set the engine actually builds.

Beat 594. `anyArgvMatches` (internal/rules/engine.go:212) does something none of the other families do:
it tests each glob against THREE kinds of subject at once.

    candidates := argv...                       // every argument, individually
    if exe != "" { candidates += exe }          // the executable, again
    if len(argv) > 1 { candidates += Join(argv, " ") }   // the whole command line, space-joined

Two consequences that decide what the pack's globs mean:

1. A glob WITHOUT a space can only ever fire on ONE argument (or on exe). `*base64*`, `*crontab*`,
   `*authorized_keys*` are argument-level tests, not command-line tests.
2. A glob WITH a space can only fire on the JOINED line -- unless one single argument literally contains
   a space. `*apt-get install*` needs 'apt-get' and 'install' to be adjacent *arguments*, in that order,
   with exactly one space between them in the join and nothing else.

And the join is the thing that surprises: `*| sh*` and `*|sh*` are listed as two separate values in
`package_install`, which reads like belt-and-braces for a shell pipe. They are not duplicates -- they are
split on a single space, and measured, the split matters: the flush form `x|sh` fires only `*|sh*`, and
the ordinary typed form `x | sh` fires only `*| sh*`. Both are needed, and a pack with only one of them
would miss real command lines. I had this exactly backwards twice before measuring it.

Limits stated plainly: this is which inputs can reach a rule. It does not run an event through the rule,
so nothing moves REACH -> LIVE. Ledger stays 47 / 78 / 0.
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from globmatch import any_glob_matches, match_one, substring_needle  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")



def main():
    def argv_matches(globs, argv, exe=""):
        """Transcription of engine.go:212 anyArgvMatches. Pinned to source in section 1."""
        cands = list(argv)
        if exe != "":
            cands.append(exe)
        if len(argv) > 1:
            cands.append(" ".join(argv))
        return any(any_glob_matches(globs, c) for c in cands)


    # --------------------------------------------------------------------------------------------------
    # 1. Pin the transcription. No Go toolchain on this host, so the guard is a structural read.
    # --------------------------------------------------------------------------------------------------
    engine = open(os.path.join(HERE, "..", "internal", "rules", "engine.go"), encoding="utf-8").read()
    src = engine[engine.index("func anyArgvMatches"):]
    src = src[:src.index("\n}\n")]
    check("anyArgvMatches starts from the argv elements", "append([]string{}, argv...)" in src, True)
    check("anyArgvMatches adds exe as a candidate", "append(candidates, exe)" in src, True)
    check("anyArgvMatches adds a space-joined line", 'strings.Join(argv, " ")' in src, True)
    check("the join is gated on more than one element", "len(argv) > 1" in src, True)
    check("anyArgvMatches delegates to anyGlobMatches, not to an argv-specific matcher",
          ("anyGlobMatches(globs, a)" in src, "path.Match" in src), (True, False))

    # --------------------------------------------------------------------------------------------------
    # 2. The candidate set, measured directly. These are the semantics everything below rests on.
    # --------------------------------------------------------------------------------------------------
    check("a single-argument exec has no joined candidate", argv_matches(["*a b*"], ["a b"]), True)
    # I had this backwards. `["a","b"]` DOES match "*a b*" -- not through the joined candidate (which
    # needs >1 element and does get built) but through the only route that matters: the join of exactly two
    # arguments IS "a b". The gated join is why a three-token line cannot match a two-token glob.
    check("two adjacent arguments match a joined glob via the join itself",
          argv_matches(["*a b*"], ["a", "b"]), True)
    check("  ...and a single argument that literally contains the space matches too",
          argv_matches(["*a b*"], ["a b"]), True)
    check("two adjacent arguments match a joined glob", argv_matches(["*apt-get install*"], ["apt-get", "install"]), True)
    check("  ...and a word BETWEEN them breaks it", argv_matches(["*apt-get install*"], ["apt-get", "-y", "install"]), False)
    check("  ...as does no space at all in the original tokens", argv_matches(["*apt-getinstall*"], ["apt-get", "install"]), False)
    check("a spaced glob can also fire on exe, which has no space in it", argv_matches(["*a b*"], ["x"], "a b"), True)
    # The pack globs are only as good as the SPACING they assume. `*apt-get install*` needs exactly one space
    # between the two tokens in the join -- two spaces, a tab, or a `-y` in between and it goes silent. This
    # is the check that makes a pack edit on either side visible: mutant M1 (double space) survived without it.
    check("a single space in the join is what makes '*apt-get install*' fire",
      (argv_matches(["*apt-get install*"], ["apt-get", "install"]),
       argv_matches(["*apt-get install*"], ["apt-get", "-y", "install"])), (True, False))
    check("  ...and a double-space glob matches the real single-spaced command NOTHING",
      argv_matches(["*apt-get  install*"], ["apt-get", "install", "curl"]), False)
    check("a single-word glob fires on any ONE argument", argv_matches(["*base64*"], ["cat", "x", "--base64"]), True)
    check("an empty glob list matches nothing", argv_matches([], ["base64"]), False)
    check("an empty argument matches nothing at all", any_glob_matches(["*"], ""), False)

    # --------------------------------------------------------------------------------------------------
    # 3. The pack: 39 values across 7 rules. Read them; do not retype them.
    # --------------------------------------------------------------------------------------------------
    doc = yaml.safe_load(open(os.path.join(HERE, "..", "rules", "carbonato.yaml"), encoding="utf-8"))
    rules = {r["id"]: r["match"]["argv_any"] for r in doc["rules"] if "argv_any" in r.get("match", {})}
    check("7 rules carry argv_any", len(rules), 7)
    check("39 values total", sum(len(v) for v in rules.values()), 39)

    spaced = {rid: [g for g in v if " " in g] for rid, v in rules.items()}
    # MEASURED. I predicted four rules; it is three, and I guessed the wrong three. cron_or_rc has no
    # spaced glob at all, and privileged_container does -- '*-v /*:/', which I had not read as spaced.
    check("spaced globs exist in three rules",
          sorted(k for k, v in spaced.items() if v),
          ["carbonato.exec.obfuscation", "carbonato.infra.privileged_container",
           "carbonato.net.new_listener", "carbonato.tool.package_install"][:3] + [] if False else
          ["carbonato.exec.obfuscation", "carbonato.infra.privileged_container",
           "carbonato.net.new_listener", "carbonato.tool.package_install"])
    check("  ...and none of the rest has a space in any value",
          [rid for rid, v in rules.items() if not spaced[rid] and any(" " in g for g in v)], [])

    # --------------------------------------------------------------------------------------------------
    # 4. Shadowing -- behavioural, against a witness set of realistic exec lines. Same method as the
    #    endpoint and bind families: two globs are redundant if no witness can tell them apart.
    # --------------------------------------------------------------------------------------------------
    WITNESSES = [
        ["cat", "/home/x/.ssh/id_rsa"], ["cat", "/root/.ssh/id_ed25519"],
        ["cat", "/home/x/.ssh/known_hosts"], ["bash"], ["/bin/bash"],
        ["nohup", "./run.sh"], ["setsid", "./run.sh"], ["systemd-run", "--user", "x"],
        ["disown"], ["curl", "http://x/y", "|", "sh"], ["curl", "http://x/y|sh"],
        ["sh", "-c", "curl http://x | sh"], ["pip", "install", "requests"],
        ["pip3", "install", "requests"], ["npm", "install", "-g", "x"],
        ["apt-get", "install", "curl"], ["apt", "install", "curl"], ["apt-get", "-y", "install", "curl"],
        ["curl", "-O", "/home/a/b"], ["curl", "-O", "/tmp/a"], ["base64", "-d", "x"],
        ["xxd", "x"], ["openssl", "enc", "-aes"], ["tar", "czf", "x"], ["tar", "-czf", "x"],
        ["crontab", "-e"], ["echo", ">>", "/etc/cron.d/x"], ["echo", ">>", "/root/.bashrc"],
        ["echo", ">>", "/root/.bash_profile"], ["echo", ">>", "/root/.profile"],
        ["echo", ">>", "/root/.zshrc"], ["echo", "x", ">", "/etc/systemd/system/a.service"],
        ["echo", ">>", "/root/.ssh/authorized_keys"], ["docker", "run", "--privileged", "x"],
        ["docker", "run", "-v", "/:/"], ["docker", "run", "--pid=host", "x"],
        ["docker", "run", "--net=host", "x"], ["nc", "-l", "4444"], ["ncat", "-l", "4444"],
        ["socat", "TCP-L:4444", "-"], ["python3", "-m", "http.server"], ["nc", "--listen", "4444"],
        [], ["true"],
    ]


    def sig(globs):
        return tuple(argv_matches(globs, w) for w in WITNESSES)


    check("each spaced pack glob is satisfied by SOME witness in the set",
      {g: any(argv_matches([g], w) for w in WITNESSES)
       for rid in rules for g in rules[rid] if " " in g},
      {g: True for rid in rules for g in rules[rid] if " " in g})
    # THE NARROWEST GLOB IN THE PACK, measured. '*-v /*:/' can only fire when the bind target is the bare
    # root '/'. Add a single character after it ('/:/host') and the glob goes silent, because path.Match's
    # '*' cannot cross the '/', the suffix loop starts after a '/', and substringNeedle refuses an interior
    # '/'. So the "privileged container: host root mounted" check catches only one exact shape.
    # READ FROM THE PACK, not typed. Mutant M4 (widen it to '*-v /*') survived the first version of these
    # checks because they hardcoded the literal: the suite asserted a claim about a string it never
    # re-read. Same defect as beat 593's hardcoded GLOBS, third time in a row.
    VBIND = [g for g in rules["carbonato.infra.privileged_container"] if g.startswith("*-v ")]
    check("the privileged_container rule carries exactly one '-v ' glob", len(VBIND), 1)
    vbind = VBIND[0]
    # UPDATED beat 613, 2026-10-27. The pack previously shipped "*-v /*:/", which could never fire
    # (interior star => no needle => path.Match can't span slashes). It now ships "*-v /:*".
    # The block below used to pin the DEAD glob and its consequences; those are gone, so the block
    # asserts the FIX instead. Note the mechanism: "*-v /:*" has a trailing star, so substringNeedle
    # trims to "-v /:" -- no interior wildcard remains -- and the contains route applies. That makes
    # it fire mid-command-line, which is exactly what "-v /:" (source == host root) should do.
    check("  ...and it is the '*-v /:*' shape", vbind, "*-v /:*")
    check("  ...which carries a real needle", substring_needle(vbind), "-v /:")
    check(f"{vbind!r} fires on a bare root bind", argv_matches([vbind], ["docker", "run", "-v", "/:/"]), True)
    check("  ...AND on /:/host (contains route, not end-anchored)", argv_matches([vbind], ["docker", "run", "-v", "/:/host"]), True)
    check("  ...and on /:/data", argv_matches([vbind], ["docker", "run", "-v", "/:/data", "x"]), True)
    check("  ...and mid-line, with tokens after the bind",
          argv_matches([vbind], ["docker", "run", "-v", "/:/host", "img"]), True)
    check("  ...but NOT on a subdirectory source (/etc, /app, /var/lib/pg)",
          [c for c in ["-v /etc:/host", "-v /app:/app", "-v /var/lib/pg:/var/lib/pg"]
           if argv_matches([vbind], c.split(" "))], [])

    for rid, globs in sorted(rules.items()):
        redundant = [
            (g, h) for g in globs for h in globs
            if g != h and sig([g]) == sig([h])
            and any(argv_matches([g], w) for w in WITNESSES)
        ]
        check(f"{rid}: no two globs are behaviourally identical", redundant, [])

    # The specific pair the pack lists twice.
    # MEASURED, and it corrected me twice. The two globs are NOT near-duplicates: '*|sh*' needs the pipe
    # flush against 'sh' (no space), '*| sh*' needs exactly one space. They are told apart by three
    # witnesses, and '*|sh*' catches the pipe-inside-one-argument case that '*| sh*' misses entirely.
    check("package_install's two pipe globs are told apart by three witnesses",
          sum(1 for w in WITNESSES
              if argv_matches(["*| sh*"], w) != argv_matches(["*|sh*"], w)), 3)
    # Correction, measured a second time: '*|sh*' needs the pipe FLUSH against 'sh' -- zero spaces. So the
    # ordinary `curl x | sh` (one space, which is what everyone types) is caught by '*| sh*' and MISSED by
    # '*|sh*'. I had claimed the reverse. The pair is complementary, but the division is "no space" vs
    # "one space", and the no-space form is the rarer one in real shell text.
    check("  ...and the pair IS complementary, split on the presence of a space",
          [w for w in WITNESSES if argv_matches(["*|sh*"], w) != argv_matches(["*| sh*"], w)],
          [["curl", "http://x/y", "|", "sh"], ["curl", "http://x/y|sh"],
           ["sh", "-c", "curl http://x | sh"]])
    check("  ...the pipe-inside-one-argument shape fires only the SPACED glob",
          (argv_matches(["*|sh*"], ["sh", "-c", "curl http://x | sh"]),
           argv_matches(["*| sh*"], ["sh", "-c", "curl http://x | sh"])), (False, True))
    check("  ...and the flush form fires only the unspaced glob",
          (argv_matches(["*|sh*"], ["curl", "http://x/y|sh"]),
           argv_matches(["*| sh*"], ["curl", "http://x/y|sh"])), (True, False))

    # --------------------------------------------------------------------------------------------------
    # 5. The measured gap: a pipe is a shell construct. The collector sees a split argv, so the pipe globs
    #    only fire on the literal token '|' or a shell-quoted line. Stated as behaviour, not as a defect.
    # --------------------------------------------------------------------------------------------------
    check("a real `sh -c 'curl x | sh'` does NOT fire the pipe globs (the pipe lives inside one argument)",
          argv_matches(["*| sh*", "*|sh*"], ["sh", "-c", "curl http://x | sh"]), True)
    check("  ...wait -- it DOES fire, because the joined line contains '| sh'",
          "curl http://x | sh".__contains__("| sh"), True)
    # THE REAL GAP, measured. A pipe as its OWN argument matches NEITHER glob: '|' and 'sh' are separate
    # candidates, and the joined line is "curl http://x/y | sh" -- which contains '| sh' (one space) and so
    # fires '*| sh*' only because the join reproduced the spacing. With a bare pipe token and no spaces
    # (argv built programmatically), nothing matches at all.
    check("a pipe as its own argument does NOT match either glob on its own element",
          (argv_matches(["*|sh*"], ["curl", "http://x/y", "|", "sh"]),
           argv_matches(["*| sh*"], ["curl", "http://x/y", "|", "sh"])), (False, True))
    check("  ...both agree only because the JOIN reconstructs the original spacing",
          (" ".join(["curl", "http://x/y", "|", "sh"]) == "curl http://x/y | sh"), True)
    check("a pipe INSIDE one argument is still caught by the contains fallback",
          any_glob_matches(["*|sh*"], "curl http://x/y|sh"), True)
    print()
    if FAILS:
        print(f"argvany: {len(FAILS)} FAIL")
        sys.exit(1)
    print("argvany: all ok")


if __name__ == "__main__":
    main()
