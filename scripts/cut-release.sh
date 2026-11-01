#!/bin/sh
# cut-release — perform the release cut that docs/PUBLISH-READINESS.md
# describes, with the rules that page states as prose enforced as code.
#
# The page says "a published tag is never moved" and "cut from the reviewed
# tree"; both are easy to get wrong by hand at 1am, and getting the tag wrong is
# invisible until a clone or a pin resolves the wrong bytes. So this refuses to:
#
#   1. cut from a dirty tracked tree           — the cut would not be the bytes CI saw;
#   2. re-use a version that already exists    — locally or on the remote;
#   3. cut a release from a branch other than main.
#
# The tag is created on the tip that was just pushed, so the tag and the pushed
# commit are the same bytes by construction.
#
# It is a DRY RUN unless --go is passed, so running it by accident prints the
# plan and changes nothing.
#
# Usage:
#   scripts/cut-release.sh <version> [remote]           # dry run, remote=origin
#   scripts/cut-release.sh --go <version> [remote]      # push main, tag, push tag
#   scripts/cut-release.sh --go --no-preflight <version> [remote]
#
# <version> is a bare release number like 0.2.0 (no leading "v"; the pinned
# convention is a bare number, see tag 0.1.0). The annotated tag is created on
# the pushed tip with the message "watchman <version>".
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/.." && pwd)
cd "$repo"

go=0
preflight=1
version=""
remote=""
for arg in "$@"; do
    case "$arg" in
        --go) go=1 ;;
        --no-preflight) preflight=0 ;;
        --help|-h) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "cut-release: unknown flag '$arg'" >&2; exit 2 ;;
        *) if [ -z "$version" ]; then version="$arg"; else remote="$arg"; fi ;;
    esac
done

[ -n "$version" ] || { echo "usage: scripts/cut-release.sh [--go] [--no-preflight] <version> [remote]" >&2; exit 2; }
[ -n "$remote" ] || remote=origin

die() { echo "cut-release: REFUSED: $*" >&2; exit 1; }

# 0. Version must look like a release number (not "v0.2.0", not "latest").
case "$version" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) die "version '$version' is not a bare release number like 0.2.0" ;;
esac
# ...and carry no tag-unsafe characters.
case "$version" in
    *[!0-9a-zA-Z.+_-]*) die "version '$version' contains characters unsafe in a tag" ;;
esac

# 1. The tree must be clean as far as tracked files go, so the cut is the
#    reviewed commit and nothing else.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "cut-release: tracked tree is dirty:" >&2
    git status --short --untracked-files=no >&2
    die "commit or stash first — a release is a claim about fixed bytes"
fi

# 2. The version must not already exist, locally or on the remote. Re-using it
#    would either no-op or move a published tag; both are wrong.
if git rev-parse -q --verify "refs/tags/$version" >/dev/null; then
    die "tag '$version' already exists locally — a published tag is never moved; cut a new version"
fi
if git ls-remote --tags --exit-code "$remote" "refs/tags/$version" >/dev/null 2>&1; then
    die "tag '$version' already exists on '$remote' — a published tag is never moved; cut a new version"
fi

branch=$(git rev-parse --abbrev-ref HEAD)
tip=$(git rev-parse --short HEAD)
[ "$branch" = "main" ] || die "on branch '$branch', not 'main' — releases cut from main"

echo "cut-release: plan"
echo "  version : $version   (tag message: watchman $version)"
echo "  branch  : $branch @ $tip"
echo "  remote  : $remote"
echo "  steps   :"
[ "$preflight" = 1 ] && echo "    make preflight            (gate the cut on the reviewed tree)"
echo "    git push $remote main"
echo "    git tag -a $version -m \"watchman $version\""
echo "    git push $remote $version"

if [ "$go" != 1 ]; then
    echo
    echo "cut-release: DRY RUN — nothing pushed. Re-run with --go to execute."
    exit 0
fi

# 3. Everything checked out. Do it, in the documented order.
if [ "$preflight" = 1 ]; then
    echo
    echo "cut-release: make preflight"
    make preflight
fi

echo
echo "cut-release: pushing $branch to $remote"
git push "$remote" main

echo "cut-release: tagging $version at $tip"
git tag -a "$version" -m "watchman $version"

echo "cut-release: pushing tag $version to $remote"
if ! git push "$remote" "$version"; then
    echo "cut-release: tag '$version' was created locally but NOT pushed." >&2
    echo "  fix the push, then: git push $remote $version   (or: git tag -d $version)" >&2
    exit 1
fi

echo
echo "cut-release: DONE — $version published from $tip."
