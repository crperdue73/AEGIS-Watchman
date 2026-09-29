#!/usr/bin/env bash
# A tiny stand-in for the `docker` CLI, so the demo can exercise the FULL
# container path — including HostConfig enrichment — with no Docker installed.
#
# The collector invokes it two ways (see internal/telemetry/docker.go):
#   docker events  --format {{json .}}      -> stream the captured events fixture
#   docker inspect --format {{json .HostConfig}} <id>  -> the container's config
#
# The config is written to say what the captured intrusion actually was: a
# privileged container with the host root mounted. That is the CARBONATO entry
# vector, and it is what makes carbonato.container.privileged_start and
# carbonato.container.host_root_mount fire.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"

case "${1:-}" in
  events)
    cat "$here/fixtures/docker-events.jsonl"
    ;;
  inspect)
    # $1=inspect $2=--format $3=<template> $4=<id>
    cat <<'JSON'
{"Privileged":true,"PidMode":"host","NetworkMode":"host","Binds":["/:/host","/var/run/docker.sock:/var/run/docker.sock"]}
JSON
    ;;
  *)
    echo "fake-docker: unsupported invocation: $*" >&2
    exit 2
    ;;
esac
