#!/usr/bin/env bash
# ebpf-preflight — report whether *this* host can BUILD and LOAD the optional
# eBPF `file.access` source. Design: docs/R2-EBPF-DESIGN.md. Deploy: docs/deploy-collector.md §2.
#
# Why this exists. eBPF is additive and optional (D1, D3, D6): the default
# `go build ./...` needs no clang, no libbpf, no kernel headers, and a host that
# cannot load the object just runs auditd-only and logs one line. So the question
# "does this host qualify?" comes up every time an operator or a CI job tries to
# turn the source on — and answering it with a hand-typed shell probe means every
# answer has to be re-trusted from scratch. This turns it into a supported
# command that prints a verdict. It changes nothing: it only reads.
#
# Usage:
#   scripts/ebpf-preflight.sh                    # report; always exit 0
#   scripts/ebpf-preflight.sh --require-build    # exit 1 if the BUILD toolchain is absent
#   scripts/ebpf-preflight.sh --require-load     # exit 1 if a LOAD prerequisite is absent
#
# Exit codes: 0 = as required, 1 = a required capability is absent, 2 = bad usage.
set -u

require_build=0
require_load=0
for arg in "$@"; do
  case "$arg" in
    --require-build) require_build=1 ;;
    --require-load)  require_load=1 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "ebpf-preflight: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

have() { command -v "$1" >/dev/null 2>&1; }
# has_cap <bit> — is capability <bit> in this process's bounding set? Reads
# CapBnd from /proc/self/status so it needs no capsh. Returns 0 (yes) / 1 (no).
has_cap() {
  local bit="$1" bnd
  bnd=$(awk '/^CapBnd:/{print $2}' /proc/self/status 2>/dev/null) || return 1
  [ -n "$bnd" ] || return 1
  (( (16#$bnd >> bit) & 1 ))
}

pass="yes"; fail="no"
mark() { [ "$1" = yes ] && printf '%s' "$2" || printf '%s' "$3"; }

say() { printf '  %-26s %s\n' "$1" "$2"; }

echo "ebpf-preflight — host capability for the optional eBPF file.access source"
echo

# ---- BUILD: what it takes to compile the CO-RE object -----------------------
echo "BUILD (compile the CO-RE object — build host only, never the fleet)"
have clang && clang_v=$(clang --version 2>/dev/null | head -1) || clang_v=""
say "clang" "$(mark "$([ -n "$clang_v" ] && echo yes || echo no)" "$clang_v" "absent")"
if [ -f /usr/include/bpf/libbpf.h ] || [ -f /usr/include/bpf/bpf_helpers.h ]; then
  bpf_hdr=yes; bpf_hdr_v="/usr/include/bpf/ present"
else
  bpf_hdr=no; bpf_hdr_v="absent (need libbpf-dev)"
fi
say "libbpf headers" "$(mark "$bpf_hdr" "$bpf_hdr_v" "$bpf_hdr_v")"
have bpftool && bpftool_v="present" || bpftool_v="absent (optional)"
say "bpftool" "$bpftool_v"
if [ "$([ -n "$clang_v" ] && echo yes || echo no)" = yes ] && [ "$bpf_hdr" = yes ]; then
  build=yes
else
  build=no
fi
say "-> BUILD possible" "$(mark "$build" "YES" "NO")"
echo

# ---- LOAD: what it takes to load + attach at runtime ------------------------
echo "LOAD (run the object on a target host)"
kver=$(uname -r)
kbase=$(printf '%s' "$kver" | grep -oE '^[0-9]+\.[0-9]+' || true)
k_major=${kbase%%.*}; k_minor=${kbase#*.}
if [ -n "$kbase" ] && { [ "$k_major" -gt 5 ] || { [ "$k_major" -eq 5 ] && [ "$k_minor" -ge 8 ]; }; }; then
  kernel=yes
else
  kernel=no
fi
say "kernel >= 5.8" "$(mark "$kernel" "$kver" "$kver (too old for ringbuf/CO-RE)")"

[ -e /sys/kernel/btf/vmlinux ] && btf=yes || btf=no
say "BTF (CO-RE types)" "$(mark "$btf" "/sys/kernel/btf/vmlinux present" "absent")"

if has_cap 39; then cap_bpf=yes; else cap_bpf=no; fi
if has_cap 38; then cap_perfmon=yes; else cap_perfmon=no; fi
if [ "$(id -u)" -eq 0 ]; then
  caps=yes; caps_v="root (has both)"
elif [ "$cap_bpf" = yes ] && [ "$cap_perfmon" = yes ]; then
  caps=yes; caps_v="CAP_BPF + CAP_PERFMON in bounding set"
else
  caps=no; caps_v="missing CAP_BPF($cap_bpf)/CAP_PERFMON($cap_perfmon); not root"
fi
say "CAP_BPF + CAP_PERFMON" "$(mark "$caps" "$caps_v" "$caps_v")"

if grep -qs ' /sys/fs/bpf ' /proc/mounts; then bpffs=yes; else bpffs=no; fi
say "bpffs mounted" "$(mark "$bpffs" "/sys/fs/bpf mounted" "not mounted (mount -t bpf bpf /sys/fs/bpf)")"

if [ "$kernel" = yes ] && [ "$btf" = yes ] && [ "$caps" = yes ] && [ "$bpffs" = yes ]; then
  load=yes
else
  load=no
fi
say "-> LOAD possible" "$(mark "$load" "YES" "NO — collector degrades to auditd-only (D6)")"
echo

# ---- The binary side: is the source even compiled in? ------------------------
echo "NOTE: LOAD also needs a binary built with '-tags ebpf'; otherwise the"
echo "      collector logs ErrEBPFUnsupported and runs auditd-only. The default"
echo "      'go build ./...' is intentionally toolchain-free (D3)."
echo
if [ "$build" = yes ] && [ "$load" = yes ]; then
  verdict="BUILD + LOAD ready"
elif [ "$load" = yes ]; then
  verdict="LOAD ready; BUILD needs clang + libbpf headers"
elif [ "$build" = yes ]; then
  verdict="BUILD ready; LOAD prerequisites missing on this host"
else
  verdict="neither BUILD nor LOAD on this host (expected for a dev box)"
fi
echo "verdict: $verdict"

rc=0
[ "$require_build" = 1 ] && [ "$build" != yes ] && rc=1
[ "$require_load" = 1 ] && [ "$load" != yes ] && rc=1
exit $rc
