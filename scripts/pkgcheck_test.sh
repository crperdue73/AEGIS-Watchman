#!/usr/bin/env bash
# pkgcheck_test.sh — proves scripts/pkgcheck.py can FAIL before anyone trusts it to pass.
#
# Why this exists: on 2026-10-22 I wrote pkgcheck.py and then proved it could fail by
# hand-building two throwaway trees in /tmp. That's a demonstration, not a test — it
# left nothing behind to re-run, and the next person has to take my word for it.
# Callie put it exactly right: the deeper question than "can this check fail?" is
# "would anyone find out if it couldn't?" This script is the answer for pkgcheck.
#
# Each case builds a minimal module in a temp dir and asserts pkgcheck's exit status
# AND that the diagnostic names the right file. A case that passes for the wrong
# reason (crash, missing file, exit 2) is a failure, not a pass.
#
# Runs with no Go toolchain. Requires python3 and bash.
#
# Exit: 0 all cases behaved, 1 a case misbehaved.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
PY="${PYTHON:-python3}"
MOD="example.com/probe"
pass=0
fail=0

# mk_case NAME -> fresh temp module with scripts/ copied in
mk_case() {
  local d
  d="$(mktemp -d)"
  mkdir -p "$d/scripts" "$d/internal/rules"
  cp "$HERE/pkgcheck.py" "$d/scripts/pkgcheck.py"
  printf 'module %s\n\ngo 1.22\n' "$MOD" > "$d/go.mod"
  echo "$d"
}

expect() {
  local name="$1" want="$2" d="$3" needle="${4:-}"
  local out rc
  out="$(cd "$d" && "$PY" scripts/pkgcheck.py 2>&1)"
  rc=$?
  if [ "$rc" -ne "$want" ]; then
    echo "FAIL [$name]: exit $rc, wanted $want"
    echo "$out" | sed 's/^/    /'
    fail=$((fail+1))
    return
  fi
  if [ -n "$needle" ] && ! printf '%s' "$out" | grep -qF "$needle"; then
    echo "FAIL [$name]: exit $rc as expected but diagnostic did not mention '$needle'"
    echo "$out" | sed 's/^/    /'
    fail=$((fail+1))
    return
  fi
  echo "ok   [$name] (exit $rc)"
  pass=$((pass+1))
}

# --- 1. clean tree must pass, and must pass WITHOUT claiming it compiles --------
d=$(mk_case); cat > "$d/internal/rules/a.go" <<'GO'
package rules
func Alpha() {}
func init() {}
GO
expect "clean tree -> 0 (and disclaims compiling)" 0 "$d" "does NOT mean the tree compiles"
rm -rf "$d"

# --- 2. duplicate top-level func ----------------------------------------------
d=$(mk_case)
printf 'package rules\nfunc Dup() {}\n' > "$d/internal/rules/a.go"
printf 'package rules\nfunc Dup() {}\n' > "$d/internal/rules/b.go"
expect "duplicate func -> 1" 1 "$d" "redeclared 'Dup'"
rm -rf "$d"

# --- 3. two init() in one package is legal, must NOT fire ----------------------
d=$(mk_case)
printf 'package rules\nfunc init() {}\n' > "$d/internal/rules/a.go"
printf 'package rules\nfunc init() {}\n' > "$d/internal/rules/b.go"
expect "two init() -> 0" 0 "$d"
rm -rf "$d"

# --- 4. methods named the same on different receivers must NOT fire -----------
d=$(mk_case); cat > "$d/internal/rules/a.go" <<'GO'
package rules
type Engine struct{}
type Timeline struct{}
func (e *Engine) Len() int { return 0 }
func (t *Timeline) Len() int { return 0 }
GO
expect "same method on 2 receivers -> 0" 0 "$d"
rm -rf "$d"

# --- 5. mutually exclusive build tags must NOT fire ---------------------------
d=$(mk_case)
printf '//go:build !ebpf\n\npackage rules\nfunc New() {}\n' > "$d/internal/rules/stub.go"
printf '//go:build ebpf\n\npackage rules\nfunc New() {}\n' > "$d/internal/rules/linux.go"
expect "exclusive build tags -> 0" 0 "$d"
rm -rf "$d"

# --- 6. case-mismatched self-import must fire --------------------------------
d=$(mk_case)
mkdir -p "$d/internal/realschema"
printf 'package realschema\n' > "$d/internal/realschema/x.go"
printf 'package rules\nimport "%s/internal/RealSchema"\n' "$MOD" > "$d/internal/rules/c.go"
expect "case-mismatched import -> 1" 1 "$d" "does not resolve case-exactly"
rm -rf "$d"

# --- 7. a module path inside a STRING LITERAL is not an import ----------------
d=$(mk_case)
printf 'package rules\nvar p = "%s/internal/NopeThatDoesNotExist"\n' "$MOD" > "$d/internal/rules/d.go"
expect "module path in string literal -> 0" 0 "$d"
rm -rf "$d"

# --- 8. correct self-import must pass ----------------------------------------
d=$(mk_case)
mkdir -p "$d/internal/realschema"
printf 'package realschema\n' > "$d/internal/realschema/x.go"
printf 'package rules\nimport "%s/internal/realschema"\n' "$MOD" > "$d/internal/rules/e.go"
expect "correct self-import -> 0" 0 "$d"
rm -rf "$d"

echo
echo "pkgcheck_test: $pass behaved, $fail misbehaved"
[ "$fail" -eq 0 ] || exit 1
echo "Reminder: a green run here proves pkgcheck's ALARM works, not that any real tree compiles."
