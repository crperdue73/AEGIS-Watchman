#!/usr/bin/env python3
"""Check that relative links in this repo's Markdown resolve.

A public repo's docs are part of the product, and a broken link is a defect a
stranger hits on their first read. This walks every tracked-ish *.md file,
extracts inline links, and fails if a relative file target does not exist.

Scope and limits, stated so the check is trusted rather than assumed:
  - Only inline links `[text](target)` are checked (that is the style this repo
    uses). Reference-style links are not.
  - http(s)://, mailto:, and pure `#anchor` links are skipped.
  - A `#fragment` on a file link is ignored: this checks that the *file* exists,
    not that the heading anchor does. Anchor validation is a separate, sharper
    check and is deliberately not claimed here.
  - `.git/` and `bin/` are skipped (build output, no docs).

Exit code 0 = all relative file links resolve; 1 = at least one is broken.
No third-party dependency: standard library only, so `make check-links` works
on the same minimal host the binaries build on.
"""

import os
import re
import sys

# [text](target) — capture the target; tolerate an optional title after the
# target ("path" "Title") which Markdown allows.
LINK_RE = re.compile(r'\[[^\]]*\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)')
SKIP_DIRS = {'.git', 'bin', 'node_modules'}


def iter_markdown(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith('.md'):
                yield os.path.join(dirpath, name)


def is_external(target):
    return (
        target.startswith(('http://', 'https://', 'mailto:', 'tel:'))
        or target.startswith('#')
    )


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    broken = []
    checked = 0
    for path in iter_markdown(root):
        with open(path, encoding='utf-8', errors='replace') as fh:
            for lineno, line in enumerate(fh, 1):
                for match in LINK_RE.finditer(line):
                    target = match.group(1)
                    if is_external(target):
                        continue
                    file_part = target.split('#', 1)[0]
                    if not file_part:
                        continue
                    checked += 1
                    resolved = os.path.normpath(
                        os.path.join(os.path.dirname(path), file_part)
                    )
                    if not os.path.exists(resolved):
                        broken.append(
                            (os.path.relpath(path, root), lineno, target)
                        )

    print(f"check-links: {checked} relative link(s) checked in {root}")
    for relpath, lineno, target in broken:
        print(f"  BROKEN {relpath}:{lineno} -> {target}")
    if broken:
        print(f"check-links: {len(broken)} broken link(s)")
        return 1
    print("check-links: ok")
    return 0


if __name__ == '__main__':
    sys.exit(main())
