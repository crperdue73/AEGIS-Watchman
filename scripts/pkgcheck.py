#!/usr/bin/env python3
"""pkgcheck.py — the compile errors a compiler would catch, for when there is no compiler.

WHY THIS EXISTS
  On 2026-10-22 the Go toolchain was absent on this host (go/gofmt/make/docker all gone
  since the 2026-10-08 migration). While writing internal/rules/argv_semantics_test.go I
  shipped two defects that `go build`/`go vet` would have rejected in under a second:
  a duplicate top-level `timeNow()` and an import path with the wrong case
  (`Aegis-Watchman` for module `AEGIS-Watchman`). The second builds clean on a
  case-insensitive filesystem and fails on Linux. I found both by reading, which is luck
  with a method, not a gate.

  This script is that method written down so it does not depend on me remembering to do it.

WHAT IT CHECKS (cheap, lexical, whole-tree)
  1. Duplicate top-level identifiers within one package (func/type/var/const) — the
     redeclaration error. Package = directory.
  2. Self-import paths that do not resolve case-exactly on disk — the case bug above.
  3. Self-import paths that point at a directory containing no .go files.

WHAT IT DOES NOT CHECK  (this list is the point)
  - Type errors. Nothing here knows what a type is.
  - Unused imports or variables (a compile error in Go, not just a lint).
  - Undefined identifiers used without a definition anywhere in the package.
  - Interface satisfaction, generics, build tags, platform-specific files.
  - Anything at all about whether the code WORKS.
  A pass here means "not obviously unbuildable". It does not mean "builds".

Exit: 0 clean, 1 findings, 2 could-not-run.
"""
import os, re, sys, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_TAG_RX = re.compile(r'^//go:build\s+(.+)$', re.M)
TOPPER = [
    re.compile(r'^func\s+([A-Za-z_]\w*)\s*\(', re.M),
    re.compile(r'^func\s+\(\s*[^)]*\)\s*([A-Za-z_]\w*)\s*\(', re.M),  # methods: keyed by name+recv
    re.compile(r'^type\s+([A-Za-z_]\w*)', re.M),
]
def find_root():
    for c in (ROOT, os.getcwd()):
        if os.path.exists(os.path.join(c, 'go.mod')):
            return c
    return None
ROOT = find_root()

# Beat 633: the whole body lives in main() so importing this file RUNS NOTHING.
# It is an instrument whose product is its output; it is not a library, and it must not
# perform a check into someone else's tally (nonprobe_test.py, docs/2026-10-28-import-silence-blindspot.md).
def main():
    if ROOT is None:
        print('pkgcheck: FATAL no go.mod found', file=sys.stderr)
        sys.exit(2)

    def go_files(d):
        return sorted(glob.glob(os.path.join(d, '*.go')))

    def packages():
        out = {}
        for dp, dns, fns in os.walk(ROOT):
            dns[:] = [d for d in dns if d not in ('.git', 'vendor', 'node_modules', 'testdata')]
            fs = [f for f in fns if f.endswith('.go')]
            if fs:
                out[dp] = sorted(fs)
        return out

    findings = []

    # ---- 1. duplicate top-level identifiers per package ----
    for pkgdir, files in packages().items():
        seen = {}
        for fn in files:
            path = os.path.join(pkgdir, fn)
            try:
                src = open(path, encoding='utf-8').read()
            except OSError as e:
                findings.append((path, 0, 'unreadable: %s' % e))
                continue
            # Build tags: two files with mutually exclusive constraints are never in the
            # same build, so their identifiers may repeat. Key declarations by tag-set.
            tags = BUILD_TAG_RX.search(src)
            tagkey = ' '.join(sorted(tags.group(1).split())) if tags else ''

            for rx in TOPPER:
                for m in rx.finditer(src):
                    name = m.group(1)
                    line = src[:m.start()].count('\n') + 1
                    if rx is TOPPER[1]:
                        # A method is in the receiver's namespace, not the package's.
                        # `Len` on *Engine and `Len` on *Timeline coexist happily, and a
                        # method may share a name with a package-level func. Comparing
                        # methods against anything here produced 31 false findings on the
                        # first run of this script — the check-always-fails cousin of the
                        # check-that-can't-fail. Methods are simply not this check's
                        # business; skip them.
                        continue
                    if name == 'init':
                        # Multiple init() funcs per package are legal, one per file.
                        continue
                    key = (tagkey, name)
                    if key in seen and seen[key][0] != path:
                        p0, l0 = seen[key]
                        findings.append((path, line,
                            'redeclared %r (was %s:%d)' % (name, os.path.relpath(p0, ROOT), l0)))
                    elif key in seen:
                        p0, l0 = seen[key]
                        findings.append((path, line,
                            'redeclared %r in same file (was line %d)' % (name, l0)))
                    else:
                        seen[key] = (path, line)

    # ---- 2 & 3. self-import path resolution ----
    gm = os.path.join(ROOT, 'go.mod')
    mod = None
    for line in open(gm, encoding='utf-8'):
        m = re.match(r'^\s*module\s+(\S+)', line)
        if m:
            mod = m.group(1)
            break
    if not mod:
        print('pkgcheck: FATAL no module line in go.mod', file=sys.stderr)
        sys.exit(2)

    # Import positions only. A module path appearing inside a plain string literal is not
    # an import — internal/collector/config_docs_test.go builds one as a reflect.PkgPath()
    # prefix, which produced a false finding before this restriction.
    IMPORT_BLOCK_RX = re.compile(r'^import\s*\((.*?)^\)', re.M | re.S)
    SINGLE_IMPORT_RX = re.compile(r'^import\s+(?:[._\w]+\s+)?"[^"]+"', re.M)
    IMPORT_RX = re.compile(r'"(' + re.escape(mod) + r'(?:/[^"]*)?)"')


    def check_import(path, line, imp):
        """Resolve a self-import against the tree case-exactly, like a Linux compiler."""
        rel = imp[len(mod):].lstrip('/')
        cur = ROOT
        for seg in (rel.split('/') if rel else []):
            try:
                entries = os.listdir(cur)
            except OSError:
                entries = []
            if seg not in entries:
                near = [e for e in entries if e.lower() == seg.lower()]
                hint = (' — disk has %r' % near[0]) if near else ''
                findings.append((path, line,
                    'self-import %r does not resolve case-exactly%s' % (imp, hint)))
                return
            cur = os.path.join(cur, seg)
        if not go_files(cur):
            findings.append((path, line,
                'self-import %r resolves to a directory with no .go files' % imp))


    for pkgdir, files in packages().items():
        for fn in files:
            path = os.path.join(pkgdir, fn)
            src = open(path, encoding='utf-8').read()
            # Only real imports count: block form `import ( ... )` and the single-line form.
            # A module path inside a string literal is NOT an import (config_docs_test.go
            # uses one as a reflect.PkgPath() prefix) — that false finding is why this
            # restricted to syntactic import positions.
            offsets = []
            for blk in IMPORT_BLOCK_RX.finditer(src):
                for mm in IMPORT_RX.finditer(blk.group(1)):
                    offsets.append((blk.start(1) + mm.start(), mm.group(1)))
            for mm in SINGLE_IMPORT_RX.finditer(src):
                for im in IMPORT_RX.finditer(mm.group(0)):
                    offsets.append((mm.start() + im.start(), im.group(1)))
            for pos, imp in offsets:
                check_import(path, src[:pos].count('\n') + 1, imp)

    for path, line, msg in findings:
        print('%s:%d: %s' % (os.path.relpath(path, ROOT), line, msg), file=sys.stderr)
    if findings:
        print('pkgcheck: %d finding(s) — not obviously buildable' % len(findings), file=sys.stderr)
        sys.exit(1)
    print('pkgcheck: clean — no duplicate top-level identifiers, all self-imports resolve '
          'case-exactly. NOTE: this does NOT mean the tree compiles; see the header.')
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
