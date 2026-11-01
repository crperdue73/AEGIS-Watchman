# FINDING 2026-10-25i — a leading `/` is the whole difference between a rule and a rumour

**Status:** OPEN — measurement only. `rules/carbonato.yaml` untouched; proposals below, not applied.
**Instrument:** `scripts/globanchor_test.py` (17 ok) against `scripts/neargo.py`; Go read, not run.

Beat 580. First pass over the 12 rules I had not audited. One clean, general result, and it is not
about binds at all.

## The rule, stated once

In this matcher a glob with a leading `*/` is anchored to a **path segment boundary**. A glob without one
matches the literal string anywhere it appears — including inside a longer filename.

```
'*/.ssh/*'        on '/home/x/.ssh/id_rsa'         True
'*id_rsa*'        on '/home/x/notid_rsabackup.txt' True   <-- not an ssh key
'*credentials*'   on '/var/log/credentials_rotation.log'  True
'*/.netrc*'       on '/home/x/.netrc.example'      True   <-- the example file
```

`*id_rsa*` is an unanchored infix. It cannot tell `id_rsa` from `notid_rsabackup`.

## Measured false positives on the two critical secret rules

`carbonato.secret.read_order_follows_constraint` and `carbonato.secret.read_after_shell` are both
**critical**. Fed files that are plainly not secrets:

| glob | fires on |
|---|---|
| `*id_rsa*` | `/srv/backup/id_rsa.tar.gz`, `/home/x/notid_rsabackup.txt` |
| `*credentials*` | `/var/log/credentials_rotation.log`, `/app/config/credentials.py`, `/tmp/test_credentials.json`, `/opt/.git-credentials.example` |
| `*/.netrc*` | `/home/x/.netrc.example` |
| `*/.git-credentials*` | `/opt/.git-credentials.example` |

`credentials.py` is a source file. `credentials_rotation.log` is a log about rotation. `.netrc.example`
is documentation. **On a critical rule, a false positive is not noise — it is how a real one gets
ignored**, and `read_after_shell` is `prior_event`-gated on a shell exec, so a `pip install` followed by
writing `credentials.py` scores as a critical credential theft.

## The inconsistency that proves it's accidental, not considered

`persist.cron_or_rc` and `persist.file_write` cover **the same four files** with different globs:

| file | `cron_or_rc` (high) | `file_write` (high) |
|---|---|---|
| `.bashrc` | `*/.bashrc*` | `*/.bashrc` |
| `.profile` | `*/.profile` | `*/.profile` |
| `.bash_profile` | `*/.bash_profile*` | `*/.bash_profile` |
| `.zshrc` | `*/.zshrc*` | `*/.zshrc` |

Note `.profile` is written **without** a trailing `*` in *both* — the odd one out, matching nothing that
isn't exactly `.../.profile`. And `cron_or_rc`'s version has the trailing `*` on three of four while
`file_write`'s has it on none. Two rules that must catch the same writes disagree about three of four
files, and neither choice is explained anywhere. That is drift, not design.

## What is genuinely fine, so the list is honest

- `persist.file_write` globs (`*/etc/cron.d/*`, `*/authorized_keys`, …) are correctly anchored to a
  segment. `*/.bashrc` requires the `/` before the dot, so `bashrc` alone doesn't match. Good.
- `exfil.egress_*` and `c2.blockchain_rpc` are endpoint globs (`*slack.com*`), where substring matching
  is the **right** semantics — a hostname is a single token, not a path. Don't "fix" these.
- `secret.read_model_key`'s `*/.docker/config.json*`, `*/openai*key*` etc. are anchored well enough for
  their purpose.

So the defect is specifically: **unanchored infix globs used where a path is meant** — 6 of them across
three critical secret rules, plus the trailing-star drift on the persistence pair.

**Corrections to my first draft, from running the classifier instead of trusting my reading:**

- `*.env` — I flagged it as unanchored and it is **not**. It starts with a literal `.`, so it's a
  suffix match anchored at end-of-string: `'/app/environment.md'` and `'/etc/environment'` correctly
  miss, `'/home/x/.env'` correctly hits. Fine as written; pinned so nobody "fixes" it.
- `*/openai*key*` — I listed it as unanchored. The char after the leading `*` is `/`, so it **is**
  anchored. Only `*anthropic*key*` is not.
- The unanchored inventory is exactly: `read_order_follows_constraint` → `*id_rsa*`, `*id_ed25519*`,
  `*credentials*`; `read_after_shell` → `*id_rsa*`, `*id_ed25519*`; `read_model_key` →
  `*anthropic*key*`.

Three times tonight a test I wrote refused a number or a classification I'd produced from memory. That
is the test doing the job I built it for.

## Proposed, not applied

Change `*id_rsa*` → `*/id_rsa*`, `*credentials*` → `*/credentials*` (and consider whether a bare
`credentials` basename is even a secret signal — it usually isn't), `*/.netrc*` → `*/.netrc`,
`*/.git-credentials*` → `*/.git-credentials`. Then make the two persistence rules agree.

**Not applied.** This is the shipped rule pack on a critical detector; every one of those is a
behaviour change that trades one kind of miss for another, and the pack owner decides which misses are
acceptable. Attached: the measurements above, which make the trade explicit instead of a judgement call.

Pack untouched: **117 / 39 / 78**.
