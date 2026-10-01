# watchman — the collector and central service in one small image.
#
# Build (from the repository root):
#
#   docker build -f deploy/docker/Dockerfile -t watchman .
#
# The image is distroless: no shell, no package manager, unprivileged — that is
# the point. It carries all four binaries and the shipped rule pack, so Layer 3
# has something to load out of the box. Configs are NOT baked in (they are
# host-specific); mount them.
#
# Run the collector — mount the config, the audit log it tails, and the agent
# workspaces it hashes. Mount configs at a path OTHER than /etc/watchman: a bind
# mount there would shadow the rule pack this image bakes at /etc/watchman/rules,
# and Layer 3 would fail to load ("rules_dir ... no such file or directory").
#
#   docker run --rm \
#     -v /etc/watchman:/config:ro \
#     -v /var/log/audit/audit.log:/var/log/audit/audit.log:ro \
#     -v /home:/home:ro \
#     watchman -config /config/collector.yaml
#
# Run central instead (override the entrypoint) and persist its stores:
#
#   docker run --rm -p 8080:8080 \
#     -v /etc/watchman:/config:ro \
#     -v watchman-state:/var/lib/watchman \
#     --entrypoint /watchman-central \
#     watchman -config /config/central.yaml
#
# For mutual TLS (the default for any real fleet), also mount the cert directory
# and put both containers on one network so the collector can reach central by
# name. The image runs as uid 65532 (nonroot), so the mounted key/config files
# must be readable by it. Full walkthrough: deploy/README.md §"Mutual TLS".
#
# The collector needs no root and no capabilities. Two optional sources do:
#   - eBPF file.access  -> --cap-add=BPF --cap-add=PERFMON, and a binary built
#     with `-tags ebpf` (see docs/deploy-collector.md §2);
#   - the Docker adapter -> mount the host's /var/run/docker.sock.
# The container has no `docker` CLI, so the Docker adapter's inspect enrichment
# is unavailable inside it — the events still fire, with identity only.

FROM golang:1.22-bookworm AS build
WORKDIR /src
# The project is pure Go and targets a distroless (no-libc) runtime, so the
# binaries MUST be static. Left to default, a Go build on a host with a C
# compiler enables cgo and links glibc — the result fails at container start
# with a bare "exec /watchman-collector: no such file or directory" (the
# kernel cannot find the interpreter). CGO_ENABLED=0 is therefore load-bearing,
# not tidiness.
ENV CGO_ENABLED=0
# Cache the module download on its own layer so a source edit does not re-fetch.
COPY go.mod go.sum ./
RUN go mod download
COPY . .
# Stamp a version. With no .git in the build context, the Makefile's git-derived
# defaults are not available, so pass them explicitly (override with --build-arg).
ARG VERSION=docker
ARG COMMIT=unknown
ARG DATE=unknown
RUN make build VERSION="${VERSION}" COMMIT="${COMMIT}" DATE="${DATE}"
# Pre-create the runtime state dir owned by the image's unprivileged user, so a
# mounted volume inherits the right ownership and the service can write to it.
RUN mkdir -p /state && chown 65532:65532 /state && touch /state/.keep

FROM gcr.io/distroless/static-debian12:nonroot
LABEL org.opencontainers.image.title="watchman" \
      org.opencontainers.image.description="Carbonato-class agent intrusion detection — collector + central" \
      org.opencontainers.image.source="https://github.com/CRPerdue/watchman" \
      org.opencontainers.image.licenses="Apache-2.0"

COPY --from=build /src/bin/watchman-collector /watchman-collector
COPY --from=build /src/bin/watchman-central   /watchman-central
COPY --from=build /src/bin/watchman-manifest  /watchman-manifest
COPY --from=build /src/bin/watchman-rules     /watchman-rules
# The shipped rule pack, so a default Layer 3 config has rules to load.
COPY --from=build /src/rules /etc/watchman/rules
# Durable stores / spool. Mount a named volume here to survive a restart.
COPY --from=build --chown=65532:65532 /state /var/lib/watchman
VOLUME ["/var/lib/watchman"]

EXPOSE 8080
# Collector by default; `--entrypoint /watchman-central` runs the service.
ENTRYPOINT ["/watchman-collector"]
