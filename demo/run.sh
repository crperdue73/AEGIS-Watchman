#!/usr/bin/env bash
# agentids — runnable end-to-end demo.
#
# Reproduces the CARBONATO chain and the container entry vector through the real
# collector, the real rules engine, and the real central service — with NO root,
# NO live auditd, and NO Docker daemon. Everything comes from the captured
# fixtures in ./fixtures/ and the configs beside them.
#
#   ./run.sh          run the demo, print the alerts, tear down
#
# What you should see: six rule matches, two of them critical — the privileged,
# host-mounted container (the entry vector) and the egress-after-identity-change
# correlation (the chain's spine).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"

repo="$(cd .. && pwd)"
bin_dir="$repo/bin"
port=18080

# --- build (always) ----------------------------------------------------------
# Always build from the current source. `go build` is incremental, and a demo
# that silently runs a stale binary is worse than one that takes a second longer.
echo "== building binaries (make build) =="
(cd "$repo" && make build)

# --- fresh state -------------------------------------------------------------
rm -rf out
mkdir -p out
# A pristine persona file copy each run, so Layer 1 has something to watch.
git checkout -- agent-root/SOUL.md 2>/dev/null || true

central_pid=""
cleanup() {
  if [[ -n "$central_pid" ]] && kill -0 "$central_pid" 2>/dev/null; then
    kill "$central_pid" 2>/dev/null || true
    wait "$central_pid" 2>/dev/null || true
  fi
  # Restore the stand-in persona file so the demo never leaves a dirty tree —
  # pass 2 appends an "attacker" edit to it on purpose.
  git checkout -- agent-root/SOUL.md 2>/dev/null || true
}
trap cleanup EXIT

echo "== starting central on :$port =="
"$bin_dir/agentids-central" -config central.yaml > out/central.log 2>&1 &
central_pid=$!

# Wait for the port to accept.
for _ in $(seq 1 50); do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then break; fi
  sleep 0.1
done

echo
echo "== pass 1: enroll the agent and replay the captured chain =="
"$bin_dir/agentids-collector" -config collector.yaml -once \
  -host-id demo-host -host-name demo 2>&1 | sed 's/^/   /'

echo
echo "== pass 2: an intruder edits the persona file, then activity resumes =="
# Touch the persona so the next pass produces identity.drift (Layer 1), which the
# collector feeds to the timeline before Layer 3 evaluates — that ordering is
# what lets the egress-after-change correlation fire.
printf '\n<!-- overwritten by attacker -->\n' >> agent-root/SOUL.md
"$bin_dir/agentids-collector" -config collector.yaml -once \
  -host-id demo-host -host-name demo 2>&1 | sed 's/^/   /'

echo
echo "== alerts as central saw them =="
grep -E "ALERT:" out/central.log | sed 's/^/   /'

echo
echo "== fleet view: the operator console, not a log grep =="
echo "   GET /api/v1/fleet"
fleet_json="$(curl -s "http://127.0.0.1:$port/api/v1/fleet")"
if command -v python3 >/dev/null 2>&1; then
  printf '%s' "$fleet_json" | python3 -c '
import json, sys
v = json.load(sys.stdin)
hosts = v.get("hosts", [])
print("   %d event(s) across %d host(s)" % (v.get("total_events", 0), len(hosts)))
for h in hosts:
    name = h.get("host_name") or h.get("host_id")
    print("   %s (%s): %d event(s), max severity %s" % (
        name, h.get("host_id"), h.get("total", 0), h.get("max_severity")))
    tech = h.get("by_technique") or {}
    if tech:
        print("      by technique: " + ", ".join(
            "%s=%d" % (k, tech[k]) for k in sorted(tech)))
    src = h.get("by_source") or {}
    if src:
        print("      by source: " + ", ".join(
            "%s=%d" % (k, src[k]) for k in sorted(src)))
'
else
  printf '   %s\n' "$fleet_json"
fi
echo "   the same rollup as a human page: http://127.0.0.1:$port/  (GET /)"

echo
echo "done. full central log: demo/out/central.log"
