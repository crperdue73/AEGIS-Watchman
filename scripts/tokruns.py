"""Token-boundary runs: the sub-slices of a string a glob may be anchored against.

A "run" is a contiguous slice of a path whose boundaries fall on '/' or '.'.
Rationale in one line: `*/etc/cron*` cannot match `/etc/cron.d/evil` because Go's path.Match
'*' will not cross '/'; but if '.' ends a name — `cron` then `d` — the glob can match
`/etc/cron` and let '*' eat `.d`.

Pure functions, no I/O, so a probe can be tested without a pack.

WHAT THIS IS NOT
  The Go engine has no notion of a run. This is a proposal about where a name may end, not a
  description of path.Match. A probe built on it measures whether the idea is worth
  implementing.
"""

BOUNDARIES = ("/", ".")


def _pieces(s):
    """(component, dotted) pairs. A leading '/' rides on the first component."""
    if not s:
        return []
    root = s.startswith("/")
    comps = s.lstrip("/").split("/")
    out = []
    for c in comps:
        if c == "":
            out.append(("", False))
        else:
            out.append((c, True))
    if not out:
        return []
    if s.startswith("/"):
        first, _ = out[0]
        out[0] = ("/" + first, True)
    return out


def runs(s, dot=True):
    """Every proper contiguous run of s bounded by '/' (and '.' if dot=True).

    'etc/cron.d/evil' -> ['etc', 'etc/cron', 'etc/cron.d', 'cron', 'cron.d', 'cron.d/evil',
                          'd', 'd/evil', 'evil', ...]   (the whole string is excluded: the
    caller already tests it)

    A run is (start component, truncation of it) followed by whole or truncated following
    components. No run ever rejoins across '.' with '/', so '*'-crossing semantics are
    unchanged — only the anchor set grows.
    """
    if not s:
        return []
    root = s.startswith("/")
    body = s[1:] if root else s
    comps = body.split("/")
    out, seen = [], set()

    def add(x):
        if x and x != s and x not in seen:
            seen.add(x)
            out.append(x)

    for i, comp in enumerate(comps):
        starts = comp.split(".") if dot else [comp]
        for t0 in range(1, len(starts) + 1):
            head = ".".join(starts[:t0])
            if i == 0 and root:
                head = "/" + head
            add(head)
            wave = [head]
            for j in range(i + 1, len(comps)):
                nxt = []
                for w in wave:
                    pieces_j = comps[j].split(".") if dot else [comps[j]]
                    for t in range(1, len(pieces_j) + 1):
                        nxt.append(w + "/" + ".".join(pieces_j[:t]))
                wave = nxt
                for w in wave:
                    add(w)
            # the trailing part of a dotted component is a run of its own ('d' for cron.d),
            # keeping the path's leading '/', never an invented interior prefix
            if dot and len(starts) > 1:
                for t0 in range(1, len(starts)):
                    tail = ".".join(starts[t0:])
                    if i == 0 and root:
                        tail = "/" + tail
                    add(tail)
    return out


def argv_runs(argv, dot=True):
    """Runs across an argv: each token, plus the joined line, split on '/' and '.'."""
    cands = list(argv)
    if len(argv) > 1:
        cands.append(" ".join(argv))
    out, seen = [], set()
    for c in cands:
        for r in runs(c, dot):
            if r not in seen:
                seen.add(r)
                out.append(r)
    return out
