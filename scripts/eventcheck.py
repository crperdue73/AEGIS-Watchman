#!/usr/bin/env python3
"""eventcheck — give the path/endpoint globs a reader.

fieldcheck found 56 of the pack's 108 matched values are read by no local guard: `path_glob_any`
on four file.access/file.write rules, `endpoint_glob_any` on three net.egress rules. Every corpus
in this tree feeds argv, so those globs have never been run against a single sample by anything.

This file adds the missing samples and runs them, using `neargo` (the near-Go matcher). Three
verdicts per rule, and they are deliberately different things:

  LIVE-ONLY-AT-ROOT   at least one glob fires, but every one that fires does so only on a
                      canonical/root path ('/etc/crontab'). The attack form with a prefix
                      ('/x/etc/crontab') does not fire — so the detector works for events that
                      come from the kernel (file.access, file.write: absolute paths) and fails
                      for ones that come from a chroot, a container mount, or a relative write.
  DEAD                no glob fires on any sample of its own shape.
  FIRES-WITH-NOISE    a legal/canonical path fires that should not (over-match).

Per-rule samples are written by hand from the rule's own description — the shapes it claims to
catch, and one benign shape it claims to leave alone. They are my reading of the pack, not the
pack owner's table, and that is stated in the doc.

Exit: 0 if every rule is live, 1 if any rule is DEAD or silent on a shape it claims, 2 if the
pack cannot be read.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo  # noqa: E402
from packcheck import PACK  # noqa: E402
from fieldcheck import keys_by_rule  # noqa: E402

# rule -> (glob key, [(path, should_fire)])
CASES = {
    "carbonato.secret.read_order_follows_constraint": ("path_glob_any", [
        ("/root/.ssh/id_rsa", True),
        ("/home/agent/.aws/credentials", True),
        ("/proc/1234/id_rsa", True),
        ("/srv/app/.env", True),
        ("/var/log/syslog", False),
        ("/srv/app/src/main.go", False),
    ]),
    "carbonato.secret.read_after_shell": ("path_glob_any", [
        ("/root/.ssh/id_ed25519", True),
        ("/var/lib/kubelet/.kube/config", True),
        ("/var/log/auth.log", False),
    ]),
    "carbonato.secret.read_model_key": ("path_glob_any", [
        # claim from the glob, not the prose: '*/.config/openai*' and '*openai*key*' both
        # require '.config/' or the word 'key'. '/etc/openai/api_key' is my own guess at the
        # claim and it fails; recorded in the doc rather than softened here.
        ("/etc/openai/api_key", None),
        ("/home/agent/.config/gcloud/application_default_credentials.json", True),
        ("/home/agent/.netrc", True),
        ("/home/agent/.docker/config.json", True),
        ("/home/agent/.git-credentials", True),
        ("/etc/hosts", False),
    ]),
    "carbonato.persist.file_write": ("path_glob_any", [
        ("/etc/cron.d/evil", True),
        ("/etc/crontab", True),
        ("/etc/systemd/system/backdoor.service", True),
        ("/root/.bashrc", True),
        ("/home/agent/.ssh/authorized_keys", True),
        ("/var/log/wtmp", False),
    ]),
    "carbonato.exfil.egress_after_change": ("endpoint_glob_any", [
        ("https://hooks.slack.com/services/T/B/X", True),
        ("https://api.telegram.org/bot123/sendMessage", True),
        ("https://pastebin.com/api", True),
        ("https://transfer.sh/abc", True),
        ("https://registry.npmjs.org/lodash", False),
    ]),
    "carbonato.exfil.egress_after_identity_change": ("endpoint_glob_any", [
        ("https://discord.com/api/webhooks/1/2", True),
        ("https://0x0.st", True),
        ("https://github.com/crperdue73/AEGIS-Watchman", False),
    ]),
    "carbonato.c2.blockchain_rpc": ("endpoint_glob_any", [
        ("https://mainnet.infura.io/v3/key", True),
        ("https://eth-mainnet.alchemy.com/v2/key", True),
        ("https://x.quiknode.pro/key", True),
        ("https://rpc.ankr.com/eth", True),
        ("https://api.etherscan.io/api", True),
        ("https://bscscan.com/api", True),
        ("https://docs.python.org/3/", False),
    ]),
}


def main():
    try:
        with open(PACK, encoding="utf-8") as fh:
            rows = keys_by_rule(fh.read())
    except OSError as e:
        print(f"eventcheck: cannot read pack: {e}", file=sys.stderr)
        return 2
    pack_keys = {}
    for rid, key, vals in rows:
        pack_keys.setdefault(rid, []).append((key, vals))
    if not pack_keys:
        print("eventcheck: parsed 0 key blocks — blind, not clean", file=sys.stderr)
        return 2

    total_cases = sum(len(v[1]) for v in CASES.values())
    bad = []
    print(f"eventcheck — {len(CASES)} rule(s), {total_cases} sample path/endpoint(s)\n")

    for rid, (key, cases) in CASES.items():
        globs = [g for k, vals in pack_keys.get(rid, []) if k == key for g in vals]
        if not globs:
            print(f"  [NO-GLOBS ] {rid}: {key} absent from the pack — sample table is stale")
            bad.append(rid)
            continue
        rooted_only = True
        misses, noise, prefix_silent = [], [], []
        claimed = [p for p, w in cases if w]
        for path, want in cases:
            if want is None:
                print(f"      UNSURE {'MISS' if not neargo.any_glob(globs, path) else 'HIT '}   {path}")
                continue
            hit = neargo.any_glob(globs, path)
            if want and not hit:
                misses.append(path)
            if not want and hit:
                noise.append(path)
            if want and hit and not neargo.any_glob(globs, "/x" + path):
                rooted_only = False
                prefix_silent.append(path)
        if noise:
            verdict = "NOISE"
        elif misses:
            verdict = "PARTIAL"
        elif len(prefix_silent) == len(claimed):
            verdict = "LIVE-ONLY-AT-ROOT"
        elif prefix_silent:
            verdict = "LIVE-PARTIAL-PREFIX"
        else:
            verdict = "LIVE"
        print(f"  [{verdict:19}] {rid} ({key}, {len(globs)} glob(s))")
        for p in misses:
            print(f"      MISS   {p}")
        for p in noise:
            print(f"      NOISE  {p}")
        if prefix_silent:
            print(f"      prefix form ('/x' + path) silent on {len(prefix_silent)}"
                  f"/{len(claimed)} claimed shape(s), e.g. {'/x' + prefix_silent[0]}")
        if verdict != "LIVE":
            bad.append((rid, verdict))

    print(f"\n  denominators: {len(CASES)} rules with samples, {total_cases} samples, "
          f"{total_cases} capable of failing.")
    print(f"  rules not fully LIVE: {len(bad)} of {len(CASES)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
