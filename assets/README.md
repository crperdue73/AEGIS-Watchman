# README figures

Seven chapter-break graphics for the top-level README. Each figure is built in
two layers, on purpose:

- **A text-free plate.** The generated art carries *no text at all* — no letters,
  numbers, labels, or UI micro-text. A generator that never draws a string cannot
  invent a field, a rule id, or a version the product does not emit.
- **A grounded overlay.** Every string that *is* drawn on the plate comes from a
  single reviewed label list, and every label is traceable to the code or docs it
  names (`internal/schema/`, `rules/carbonato.yaml`, this README, `docs/*`). If a
  label is not traceable, it does not render.

That is the whole rule: the figure may only say what the product already says.

Palette: near-black `#0d1117` · graphite panels `#161b22`/`#21262d` · cool
slate/cyan `#58a6ff` / `#79c0ff` / `#2dd4bf` · muted amber `#d29922` · restrained
crimson `#f85149`.

| File | Figure |
|---|---|
| `01-hero.jpg` | Hero / social preview — *scan for the action, not the package* |
| `02-carbonato-chain.jpg` | The CARBONATO attack chain |
| `03-three-layers.jpg` | Three detection layers |
| `04-architecture.jpg` | Collector → Central deployment shape |
| `05-behavior-rules.jpg` | Behavior-rules pipeline |
| `06-fleet-dashboard.jpg` | Fleet view and agent roster |
| `07-coverage-matrix.jpg` | Coverage matrix — the empty cells are the point |
