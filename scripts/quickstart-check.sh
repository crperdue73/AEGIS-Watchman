#!/usr/bin/env bash
# quickstart-check — execute the commands docs/quickstart.md tells a stranger to
# run, and fail if any of them does not work on a clean checkout.
#
# Why this exists. Heartbeat 76 found a defect no unit test could: three files
# the quickstart and demo depend on (`examples/collector.local.yaml`,
# `examples/central.local.yaml`, `demo/fixtures/audit.log`) were present in the
# working tree but *untracked* — `.gitignore` caught them — so a fresh clone was
# missing them and the quickstart's first command died with `no such file`. The
# demo job in CI already guards the demo path; nothing exercised the quickstart
# path. This is that guard, and it is the general form: a doc's copy-paste block
# is only true if it is *run*. It needs no root, no auditd, no Docker daemon.
#
# Usage: scripts/quickstart-check.sh        (exit 0 = the quickstart works)
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
cd "$repo"

bin="bin/agentids-collector"
central_bin="bin/agentids-central"

fail() { echo "quickstart-check: FAIL: $*" >&2; exit 1; }

cleanup() {
  if [[ -n "${cpid:-}" ]] && kill -0 "$cpid" 2>/dev/null; then
    kill "$cpid" 2>/dev/null || true
    wait "$cpid" 2>/dev/null || true
  fi
  # The local configs write here; leave the tree the way we found it.
  rm -f demo/out/local-baselines.json demo/out/local-records.json
}
trap cleanup EXIT

echo "== quickstart §1: build =="
make build >/dev/null
for b in agentids-collector agentids-central agentids-manifest agentids-rules; do
  [[ -x "bin/$b" ]] || fail "make did not produce bin/$b"
done
echo "   four binaries built"

echo "== quickstart §3: dry-run (needs nothing running) =="
"$bin" -config examples/collector.local.yaml -once -dry-run >/tmp/qsc-dryrun.out 2>&1 \
  || fail "the dry-run command from quickstart §3 exited non-zero (output: /tmp/qsc-dryrun.out)"
grep -q "rules ok" /tmp/qsc-dryrun.out || fail "dry-run did not report the rule pack as ok"
echo "   dry-run validated config + rule pack"

echo "== quickstart §3: the two-terminal flow =="
rm -f demo/out/local-baselines.json demo/out/local-records.json
"$central_bin" -config examples/central.local.yaml >/tmp/qsc-central.log 2>&1 &
cpid=$!
# Wait for the port the .local config names (:8080) to accept.
for _ in $(seq 1 100); do
  if (exec 3<>/dev/tcp/127.0.0.1/8080) 2>/dev/null; then break; fi
  kill -0 "$cpid" 2>/dev/null || fail "central exited before listening (see /tmp/qsc-central.log)"
  sleep 0.1
done
(exec 3<>/dev/tcp/127.0.0.1/8080) 2>/dev/null || fail "central never listened on :8080"
grep -q "listening on :8080" /tmp/qsc-central.log || fail "central log does not show it listening"

"$bin" -config examples/collector.local.yaml -once >/tmp/qsc-collector.out 2>&1 \
  || fail "the collector command from quickstart §3 exited non-zero (output: /tmp/qsc-collector.out)"

# The collector submits the agent inventory on first contact, so central must
# have written its baseline store. If it did not, the two-terminal flow is not
# actually connected — the exact thing the doc promises and a fresh clone breaks.
[[ -s demo/out/local-baselines.json ]] || fail "central wrote no baseline; the two-terminal flow did not connect"
echo "   central accepted the collector's submission (baseline store written)"

echo
echo "quickstart-check: PASS — the documented commands work on a clean checkout"
