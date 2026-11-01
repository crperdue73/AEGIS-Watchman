#!/bin/sh
# license-check — the tracked LICENSE must be canonical Apache-2.0, and the
# only thing it may say that differs is the copyright holder's own line.
#
# Why this exists. On 2026-10-01 the first public push shipped a LICENSE that
# *looked* like Apache-2.0 but was not: the copyright notice and the "Licensed
# under..." boilerplate had been pasted between the title and "TERMS AND
# CONDITIONS", where they do not belong (Apache puts them in the APPENDIX).
# GitHub's license detector read the file as NOASSERTION / "Other", so the repo
# did not actually publish under the license it claimed. A human reading the
# diff saw the word "Apache" and moved on; a machine that diffs against the
# reference text would not have. This is that machine.
#
# The check is self-contained: it normalises LICENSE by masking the single
# copyright line and stripping trailing whitespace, then compares the SHA-256 of
# the result to the hash of the Apache-2.0 reference text under the same
# transform. Any other deviation — moved boilerplate, a dropped section,
# reflowed wording, a missing trailing newline — changes the hash and fails.
#
# Usage: scripts/license-check.sh        (exit 0 = LICENSE is canonical)
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
cd "$repo"

# sha256 of the canonical Apache-2.0 text with the APPENDIX copyright line
# masked to __COPYRIGHT__ and trailing whitespace stripped.
EXPECT="f27cd923343e0e15bd72cfab09375f5efb8f88258e465083f4954e01bf0b0455"

if [ ! -f LICENSE ]; then
    echo "license-check: FAIL — no LICENSE file at the repo root"
    exit 1
fi

# The copyright line must exist and be filled in — not the [yyyy]/[owner]
# template Apache ships in the APPENDIX.
if ! grep -qE '^   Copyright [^[]' LICENSE; then
    echo "license-check: FAIL — APPENDIX copyright line is missing or still the"
    echo "    '[yyyy] [name of copyright owner]' placeholder"
    exit 1
fi

got="$(sed -E 's/^   Copyright .*$/__COPYRIGHT__/' LICENSE \
       | sed -E 's/[[:space:]]+$//' \
       | sha256sum | awk '{print $1}')"

if [ "$got" != "$EXPECT" ]; then
    echo "license-check: FAIL — LICENSE is not the canonical Apache-2.0 text"
    echo "    expected: $EXPECT"
    echo "    got:      $got"
    echo "    fix: restore the reference Apache-2.0 text and put the copyright"
    echo "         line in the APPENDIX (see the text at"
    echo "         https://www.apache.org/licenses/LICENSE-2.0.txt )"
    exit 1
fi

echo "license-check: PASS — LICENSE is canonical Apache-2.0 (copyright line filled in)"
