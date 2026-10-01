#!/usr/bin/env bash
# container-check — build deploy/docker/Dockerfile and assert, on the real
# image, every claim the Dockerfile header and deploy/README.md make about it.
#
# Why this exists. Heartbeat 66 found that the container image had never been
# actually built: it copied a dynamically-linked binary into a distroless
# (no-libc) runtime, which cannot exec — a defect invisible in a Dockerfile
# that is only read, never run. That was fixed and verified by hand. A hand
# check is not a guard: nothing stopped the same class from returning. This is
# that guard, and it is the general form — a container doc's claims are only
# true if a container build settles them.
#
# It requires a Docker daemon (unlike demo/ and quickstart-check, which need
# none). Everything else about the image — unprivileged uid, no shell, static
# binaries — follows from the base image and the run below.
#
# Usage: scripts/container-check.sh        (exit 0 = the image's claims hold)
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
cd "$repo"

image="agentids:container-check"
fail() { echo "container-check: FAIL: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 \
  || fail "docker not found — this check needs a Docker daemon (unlike demo/quickstart-check)"

cleanup() {
  docker rmi -f "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "== build: deploy/docker/Dockerfile =="
docker build -f deploy/docker/Dockerfile -t "$image" . >/tmp/cc-build.log 2>&1 \
  || { tail -20 /tmp/cc-build.log >&2; fail "docker build failed (full log: /tmp/cc-build.log)"; }
echo "   image built"

# 1. Unprivileged. The Dockerfile promises uid 65532 (distroless nonroot); a
#    regression here would run the collector as root.
uid="$(docker image inspect "$image" --format '{{.Config.User}}')"
[[ "$uid" == "65532" ]] || fail "image runs as uid '$uid', expected 65532 (nonroot)"
echo "   runs as nonroot (uid 65532)"

# 2. The shipped rule pack is baked in and loads. This is the claim "Layer 3 has
#    something to load out of the box"; a mount that shadows /etc/agentids (see
#    caveat 3) or a missing COPY would make it fail to load.
out="$(docker run --rm --entrypoint /agentids-rules "$image" verify /etc/agentids/rules 2>&1)" \
  || { echo "$out" >&2; fail "the baked rule pack did not validate at /etc/agentids/rules"; }
echo "$out" | grep -q 'rule(s)' || fail "rules verify produced no rule count: $out"
echo "   baked rule pack validates ($(echo "$out" | head -1))"

# 3. The documented shadow caveat is real: a bind mount at /etc/agentids hides
#    the baked pack, which is *why* deploy/README.md tells you to mount configs
#    at /config. If this ever stops failing, the warning is wrong.
shadow="$(mktemp -d)"
echo "config: {}" >"$shadow/collector.yaml"
if docker run --rm -v "$shadow:/etc/agentids" --entrypoint /agentids-rules \
     "$image" verify /etc/agentids/rules >/tmp/cc-shadow.out 2>&1; then
  rm -rf "$shadow"
  fail "mounting at /etc/agentids did NOT hide the baked pack — the README caveat is stale"
fi
rm -rf "$shadow"; rm -f /tmp/cc-shadow.out
echo "   the '/etc/agentids shadows the baked pack' caveat still holds"

# 4. Static binaries, proven on the built artifact (the CGO_ENABLED=0 promise).
#    The image has no shell, so export the filesystem and ldd the binaries out.
tmp="$(mktemp -d)"
cid="$(docker create "$image")"
docker export "$cid" | tar -x -C "$tmp" agentids-collector agentids-central 2>/dev/null || true
docker rm "$cid" >/dev/null 2>&1 || true
for b in agentids-collector agentids-central; do
  [[ -f "$tmp/$b" ]] || { rm -rf "$tmp"; fail "image does not contain $b"; }
  # ldd exits non-zero for a static binary and prints to stderr; capture both
  # rather than pipe it, or `set -o pipefail` fails the pipeline on success.
  linkage="$(ldd "$tmp/$b" 2>&1 || true)"
  grep -q 'not a dynamic executable' <<<"$linkage" \
    || { rm -rf "$tmp"; fail "$b is dynamically linked — it cannot exec in the distroless runtime"; }
done
rm -rf "$tmp"
echo "   binaries are statically linked (no libc — exec-safe on distroless)"

echo
echo "container-check: PASS — the image builds and every documented claim holds"
