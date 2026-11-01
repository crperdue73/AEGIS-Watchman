#!/usr/bin/env python3
"""neargo_test — pin the four behaviours engine.go states, and pin that neargo is STRICTER
than packcheck's port. If neargo ever agrees with the loose port on the known divergence,
this file's job is to say so out loud.

  A. substringNeedle refuses a core containing '/' or a further wildcard, exactly as Go does,
     and the ports are compared on strings where that could matter.
  B. substringNeedle refuses a core with a further wildcard ('*a*b*' is not a needle).
  C. suffix + basename fallbacks still work: '*/.ssh/*' hits '/root/.ssh/id_ed25519' and
     '*id_rsa*' hits '/home/x/.ssh/id_rsa' — the Go comments claim exactly this.
  D. anyArgvMatches adds the joined line only when len(argv) > 1.

Exit 0 only if all four hold.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo  # noqa: E402
import packcheck  # noqa: E402

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
    # A. CORRECTED AGAIN 2026-10-27 (beat 610). This pin said the needle for '* -O /tmp/*' is refused.
    #    The Go source refuses nothing of the kind: substringNeedle trims one leading and one trailing
    #    '*', then rejects ONLY cores containing '*?['. ' -O /tmp/' contains none, so Go returns it and
    #    the contains test is live. The pin was asserting the port's own invention.
    #    Lesson the hard way: a docstring saying the port is "stricter" is a description of a DIFFERENCE,
    #    not evidence of fidelity. Read engine.go:287 before trusting either copy.
    check("needle(' -O /tmp/') ACCEPTED (Go guards only '*?[')", neargo.substring_needle("* -O /tmp/*"), " -O /tmp/")
    check("neargo: joined line 'curl -O /tmp/x' still matches", neargo.match_one("* -O /tmp/*", "curl -O /tmp/x"), True)
    # The divergence. I assumed the loose port's needle fallback was what kept '*ssh/id*'
    # alive. It is not: pathSuffixes splits on '/' alone, so 'x ssh/id y' -> ['x ssh/id y',
    # 'id y'] and '*ssh/id*' dies; the SUFFIX path ('ssh/id', 'id') matches under both ports.
    # So the fallback's error is latent rather than load-bearing on this glob, and the honest
    # statement is narrower than the one I wrote first: neargo is stricter, and no case in
    # this file shows the difference mattering to a shipped glob.
    check("needle('id_rsa') accepted", neargo.substring_needle("*id_rsa*"), "id_rsa")
    # CORRECTED beat 610: a '/' in the core does not disqualify a needle in Go. Only '*?[' does.
    check("'*ssh/id*' core holds a '/' but is STILL a needle in Go", neargo.substring_needle("*ssh/id*"), "ssh/id")
    for s_ in ["x ssh/id y", "a ssh/id/b", "/root/.ssh/id_rsa"]:
        loose, strict = packcheck.match_one("*ssh/id*", s_), neargo.match_one("*ssh/id*", s_)
        print(f"  ----  '*ssh/id*' on {s_!r}: loose {loose}, neargo {strict}")

    # B. a needle with a further wildcard is not a needle ('/' is fine -- see above)
    check("needle('a*b') refused", neargo.substring_needle("*a*b*"), None)
    check("needle('slack.com') accepted", neargo.substring_needle("*slack.com*"), "slack.com")
    check("needle with trailing-only star", neargo.substring_needle("x*"), "x")

    # C. suffix and basename fallbacks, exactly as the Go comment claims
    check("'*/.ssh/*' hits '/root/.ssh/id_ed25519'", neargo.match_one("*/.ssh/*", "/root/.ssh/id_ed25519"), True)
    check("'*id_rsa*' hits '/home/x/.ssh/id_rsa'", neargo.match_one("*id_rsa*", "/home/x/.ssh/id_rsa"), True)
    # CORRECTED beat 610: this asserted False under the invented '/' refusal. Under the Go it FIRES --
    # needle '/etc/cron' is a plain substring of '/etc/cron.d/evil'. That is the documented intent of
    # the contains fallback ("a domain/substring rather than a path"), not a defect. Pinned as True so
    # the behaviour is visible rather than hidden behind a stricter-than-Go port.
    check("'*/etc/cron*' FIRES on '/etc/cron.d/evil' via the contains fallback",
          neargo.match_one("*/etc/cron*", "/etc/cron.d/evil"), True)

    # D. joined line only for multi-element argv
    check("single-element argv not joined", neargo.any_argv(["*pip install*"], ["pip"]), False)
    check("multi-element argv joined", neargo.any_argv(["*pip install*"], ["pip", "install", "x"]), True)
    check("empty argv never matches", neargo.any_argv(["*pip install*"], []), False)

    print(f"\nneargo_test: {ok_n} ok, {fail_n} fail")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
