#!/usr/bin/env python3
"""spoolbound.py -- decide by MEASUREMENT whether `<=` vs `<` in Spool.trim is observable.

Beat 818 could not kill `spool.go:248` (`if int64(size) <= s.maxBytes`) with `<`. Rather than keep
reasoning about it, this models the two operators over the whole reachable space and reports every
cap where the surviving prefix DIFFERS. Zero divergent caps is a measurement that the mutant is
equivalent; any divergent cap is a reachable cap where trimming is one batch too generous.

REQUIRED: this must reproduce the Go trim loop line for line, or it certifies the wrong thing.
The loop budgets `len(json.Marshal(rec)) + 1` per batch and never drops the final record.
The live spool calls trim() after EACH append, which is modelled here too -- modelling trim
once over a big batch list would be a different (and wrong) experiment.

Instrument:
  python3 scripts/spoolbound.py --sizes 243 --caps 1..40      # model
  python3 scripts/spoolbound.py --measure --go-batch-bytes 243 # compare against the real engine
"""
import argparse
import sys


def trim(recs, cap, le=True):
    """Exact transcription of Spool.trim's drop loop. le=False simulates the mutant."""
    recs = list(recs)
    while len(recs) > 1:
        size = sum(len(r) + 1 for r in recs)
        if (size <= cap) if le else (size < cap):
            break
        recs = recs[1:]
    return recs


def simulate(batch, cap, n_appends, le=True):
    """Append one batch, then trim -- exactly what Spool.Append does."""
    recs = []
    for _ in range(n_appends):
        recs.append(batch)
        recs = trim(recs, cap, le)
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="243", help="comma-separated serialized batch sizes to model")
    ap.add_argument("--caps", default="1..40", help="cap range in batches, e.g. 1..40")
    ap.add_argument("--appends", type=int, default=8)
    args = ap.parse_args()

    lo, _, hi = args.caps.partition("..")
    lo, hi = int(lo), int(hi or lo)

    divergent = []
    for size in (int(s) for s in args.sizes.split(",")):
        # A batch contributes size+1 bytes (Marshal + newline). Build a synthetic record of
        # exactly that length so the model and the engine agree on payload size -- beat 818
        # shipped a version of this file whose arithmetic was right and whose payload was wrong,
        # and it "found" divergence the engine did not have.
        batch = "x" * size
        for nb in range(lo, hi + 1):
            cap = nb * (size + 1)
            real = simulate(batch, cap, args.appends, le=True)
            mut = simulate(batch, cap, args.appends, le=False)
            if real != mut:
                divergent.append((size, nb, cap, len(real), len(mut)))

    if divergent:
        print(f"DIVERGENT on {len(divergent)} cap(s) -- the mutant is LIVE, not equivalent:")
        for size, nb, cap, a, b in divergent:
            print(f"  batch={size}B cap={nb} batches ({cap}B): real keeps {a}, mutant keeps {b}")
        return 1
    print(f"no divergence: every cap in {lo}..{hi} batches (sizes {args.sizes}) "
          f"trims identically under both operators over {args.appends} appends")
    return 0


if __name__ == "__main__":
    sys.exit(main())
