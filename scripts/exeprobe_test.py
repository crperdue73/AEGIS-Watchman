#!/usr/bin/env python3
"""exeprobe_test — pin the asymmetry that exeprobe exists to demonstrate.

Checks, in order of what they protect:
  1. The pack's own terms: 13 in 2 rules, ALL bare names. If someone adds `/usr/bin/curl` to
     exe_any, this fails — and it should fail, loudly, because that term silently narrows the rule
     to one path on one host layout.
  2. The asymmetry itself, as a known answer: 'bash' fires on /bin/bash, '/bin/bash' does NOT fire
     on a bare 'bash'. That pair is the whole finding in two assertions.
  3. A copied binary: '/tmp/python3' still fires the name term 'python3' — bash-compatible reach,
     and the reason name terms are the safe shape.
  4. The parser counts an inline list and a block list the same way, and does not run past the end
     of a rule.

Exit 0 only if all hold.
"""
import io
import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exeprobe  # noqa: E402

ok_n = fail_n = 0


def check(name, got, want):
    global ok_n, fail_n
    if got == want:
        ok_n += 1
        print(f"  ok    {name} -> {got}")
    else:
        fail_n += 1
        print(f"  FAIL  {name}: got {got}, want {want}")


def main():
    with open(exeprobe.PACK, encoding="utf-8") as fh:
        rules = exeprobe.exe_terms(fh.read())
    total = sum(len(t) for _, t in rules)
    check("rules with exe_any", len(rules), 2)
    check("exe_any terms", total, 13)
    check("every term is a bare name",
          all(exeprobe.shape(t) == "name" for _, ts in rules for t in ts), True)

    # the asymmetry, both directions
    check("name term catches an absolute path", exeprobe.fires("bash", "/bin/bash"), True)
    check("path term does NOT catch a bare name", exeprobe.fires("/bin/bash", "bash"), False)
    check("path term catches only its exact path",
          (exeprobe.fires("/bin/bash", "/usr/bin/bash"), exeprobe.fires("/bin/bash", "/bin/bash")),
          (False, True))
    check("name term still catches a copied binary", exeprobe.fires("python3", "/tmp/python3"), True)

    # parser: inline vs block
    r = exeprobe.exe_terms("rules:\n  - id: a.b\n    match:\n      exe_any: [docker, podman]\n"
                           "  - id: c.d\n    match:\n      exe_any:\n        - sh\n        - bash\n")
    check("inline and block lists parsed alike", [(i, v) for i, v in r],
          [("a.b", ["docker", "podman"]), ("c.d", ["sh", "bash"])])

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = exeprobe.main()
    check("live pack exits 0", rc, 0)

    print(f"\nexeprobe_test: {ok_n} ok, {fail_n} fail")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
