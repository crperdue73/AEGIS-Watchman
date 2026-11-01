#!/usr/bin/env python3
"""redundant_test.py -- no glob in the pack is a passenger.

WHY THIS EXISTS
Beat 614 deleted `"*:/host*"` from `carbonato.container.host_root_mount`. It was a critical-severity
glob whose only effect that no other glob in the rule also had was a FALSE POSITIVE: it fired on
`-v /etc:/host`, mounting the host's /etc INTO the container -- the opposite of an escape.

That glob was invisible in a pack read. It looked exactly like the two honest globs beside it. So did
the dead glob from beat 611 (`*-v /*:/`, which could never fire at all). Dead, redundant, and
over-broad globs are indistinguishable by eye; all three look like they are catching something.

So this suite asks the question beat 614 taught, of every glob in the pack at once:

    does this glob match anything that nothing else in its family matches?

A glob with no unique witness is either doing no work (delete it) or doing only wrong work (fix it).
Either way it needs a human decision, so this test FAILS rather than guessing which.

AND THE LIMIT OF THAT QUESTION -- found by mutation-testing this file
I reinstated the deleted `"*:/host*"` to prove this suite would catch it. It did not, and the reason
matters more than the bug: that glob DOES have a unique witness. `/x:/host` is a well-formed bind that
`*:/host*` matches and `/:/` does not. Its uniqueness was never the problem -- its uniqueness came
entirely from being WRONG.

So "no unique witness" catches REDUNDANT globs. It does not catch over-broad ones, and beat 614's glob
was both. Precision needs a labelled corpus of true and false positives per rule, which does not exist
here. This suite is one of two questions, not the whole audit.

WHAT "FAMILY" MEANS
The scope of one rule's matcher: all globs under the same `path_glob_any` / `argv_any` list, or all
globs under the same key of `detail_glob_any`. Cross-rule redundancy is NOT a defect -- two rules
firing on one path is defence in depth, and they carry different alerts.

HOW WITNESSES ARE BUILT -- and the honest limit of this test
There is no corpus of real telemetry on this box, so witnesses are SYNTHESISED from each glob's own
needle (its `substring_needle` core) in several positions, plus wildcard-stripped variants. That is
enough to expose a glob fully subsumed by a sibling, and it is exactly how beat 614's redundancy was
found. It is NOT a proof that a glob fires on real traffic -- a glob can have a unique witness here
and still never meet it in production. This test bounds downstream redundancy, not upstream value.
"""

import itertools
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import neargo  # noqa: E402


def fires(glob, value):
    """engine.go anyGlobMatches for a single glob against one value."""
    return neargo.match_one(glob, value) is True


def seeds(glob, field="", key=None):
    """Plausible strings this glob could see, derived from its own needle.

    SHAPE MATTERS, and I got this wrong first. A generous bare-needle corpus let the deleted
    `"*:/host*"` survive mutation testing: seeds for `/:/` include `:/host` and `x:/host`, and `*:/host*`
    matches those while `/:/` does not -- so it looked like it had unique witnesses. But `:/host` is not
    a bind value any docker daemon emits. It is the needle, not a sample. A glob was earning its place
    against strings that cannot occur.

    So seeds are now shaped per field:
      * binds (detail key) -> "SRC:DST" pairs; the ONLY shape BindConfig produces
      * path_glob_any      -> absolute file paths
      * argv_any           -> command lines
    Same class of error as the beat-612 skewed ladder: a fixture that cannot occur proves nothing,
    whichever direction it points.
    """
    needle = neargo.substring_needle(glob)
    if field == "detail_glob_any" and key == "binds":
        if not needle:
            return []
        # A bind is always SRC:DST with both sides absolute paths. The needle usually CONTAINS the
        # ":" that separates them ("/:/", ":/host", "/var/run/docker.sock"), so a needle cannot simply
        # be dropped into one side -- that produces values no runtime emits, like "/data::/host", and
        # then a broken glob looks like it has witnesses. Build the real shape instead: split the
        # needle at its last ":" and recombine so the result is "ABS:ABS".
        if ":" in needle:
            src_part, _, dst_part = needle.rpartition(":")
            src = (src_part or "/").rstrip("/") or "/"
            if not src.startswith("/"):
                src = "/" + src
            dst = "/" + (dst_part or "").strip("/")
            return [
                f"{src}:{dst}",                 # the needle as it naturally appears
                f"{src}:{dst}/app",             # destination with a deeper path
                f"{src.rstrip('/') + '/x' if src != '/' else '/x'}:{dst}",   # source one segment deeper
                f"{src}:{dst}/sub/dir",
            ]
        return [f"/{needle.strip('/')}:/data", f"/data:/{needle.strip('/')}"]
    if field == "path_glob_any":
        if not needle:
            # No contains route: end-anchored. Only the literal tail can match.
            body = glob.strip("*")
            return [body] if body.startswith("/") else ["/" + body]
        # Files live in directories AND as flat names directly in one. Beat 612 proved the credential
        # globs reach ONLY the flat form ("/etc/openai_key"), never the deep one ("/etc/openai/api_key").
        # A corpus of deep paths alone made a known-narrow glob look redundant. Both shapes belong here.
        out = ["/etc/" + needle.strip("/"), "/srv/" + needle.strip("/")]
        if needle.endswith("/"):
            # A DIRECTORY glob ("*/.ssh/*", "*/etc/cron.d/*") only matches a path that continues past
            # the directory. "/etc/.ssh/" alone does not -- so it needs a filename appended, not a
            # flattened name. Getting this wrong is what made every cron/systemd dir glob look dead.
            out = ["/etc" + needle + "file", "/srv" + needle + "x.conf", "/home/u" + needle + "id"]
        else:
            # A FILENAME-fragment glob. Beat 612 proved the credential globs reach only the flat form.
            flat = needle.replace("/", "_").strip("_")
            if flat:
                out += ["/etc/" + flat, "/srv/" + flat + ".key"]
        return out
    needle = neargo.substring_needle(glob)
    if needle:
        return ["cmd " + needle + " --flag", needle + " --flag", "tool " + needle,
                "cmd = " + needle, "x=y " + needle]
    body = glob.strip("*")
    return ["cmd " + body, body + " --flag"]


def families(doc):
    """(rule_id, field, key, [globs]) for every matcher family in the pack."""
    for rule in doc.get("rules", []):
        match = rule.get("match") or {}
        for field in ("path_glob_any", "argv_any"):
            globs = match.get(field)
            if isinstance(globs, list) and len(globs) > 1:
                yield (rule["id"], field, None, [str(g) for g in globs])
        detail = match.get("detail_glob_any")
        if isinstance(detail, dict):
            for key, globs in detail.items():
                if isinstance(globs, list) and len(globs) > 1:
                    yield (rule["id"], "detail_glob_any", key, [str(g) for g in globs])


def passengers():
    """Every glob that matches no shape its family-mates miss."""
    with open(os.path.join(HERE, "..", "rules", "carbonato.yaml"), encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    out = []
    seen = 0
    for rule_id, field, key, globs in families(doc):
        corpus = set()
        for glob in globs:
            corpus.update(seeds(glob, field, key))
        for glob in globs:
            seen += 1
            others = [h for h in globs if h != glob]
            if not any(fires(glob, w) and not any(fires(h, w) for h in others) for w in corpus):
                out.append((rule_id, field, key, glob))
    return out, seen


def main():
    fails, total = passengers()
    if fails:
        print("FAIL these globs match nothing their family-mates don't already match:")
        for rule_id, field, key, glob in fails:
            where = f"{field}[{key}]" if key else field
            print(f"  {rule_id}  {where}  {glob!r}")
        print("\nA glob with no unique witness is either dead (delete it) or only doing wrong work")
        print("(narrow it). That is a content decision -- see beat 614's '*:/host*'. Do not silence this.")
        return 1
    print(f"ok   no passenger globs: all {total} multi-glob family members have a unique witness")
    return 0


if __name__ == "__main__":
    sys.exit(main())
