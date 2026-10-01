# Contributing

Thanks for looking at agentids. This is a public, Apache-2.0 project — build
like it's going public, because it is.

## Ground rules

- **The event schema is a public contract.** Treat changes as breaking; see
  [`docs/event-schema.md`](docs/event-schema.md) for the versioning policy.
- **Alert-only in v1.** No auto-remediation, ever, without an explicit opt-in
  design reviewed by a maintainer.
- **One detection layer per change where possible.** Easier to review, easier to
  test.
- **Every rule needs a test fixture.** A rule without a fixture is a rumor.
  Write rules per [`docs/rules.md`](docs/rules.md).
- **No telemetry leaves the host it runs on except to the configured central
  endpoint.** Privacy is a feature.

## Development

- Language: Go (collector, central, and the rule/manifest CLIs; single static
  binaries, cross-compiles, good eBPF + cloud-native ecosystem).
- Toolchain: Go 1.22+.
- Build: `make build` (see [`Makefile`](Makefile)).
- Test: `make test`.
- CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs, on every push
  and PR: build, `vet` and `test` in **both** build modes (default and
  `-tags sqlite`), `make lint`, the end-to-end `demo/run.sh`, the doc's own
  commands via `make quickstart-check`, the container image via
  `make container-check`, and the publish scan via `make publish-scan`. Run the
  same before you push — it is the same thing, just slower.
- Publish scan: `make publish-scan` (`scripts/publish-scan.sh`) fails if any
  tracked file carries a credential, a real operator home path, or private
  infrastructure. The repo is public and was built in private; this is the
  guard. See [`docs/PUBLISH-READINESS.md`](docs/PUBLISH-READINESS.md).
- Container: `make container-check` builds `deploy/docker/Dockerfile` and
  asserts the claims the container docs make about the resulting image (nonroot
  uid 65532, the baked rule pack loads, the `/etc/agentids` shadow caveat,
  static binaries). It needs a Docker daemon; `demo/` and `quickstart-check` do
  not. A container doc that is only read, never built, is how the image shipped
  broken once.
- Lint: `make lint` — `go vet` plus `make check-links`, which fails if a
  relative link in any Markdown file does not resolve. A broken doc link is a
  defect a stranger hits on their first read, so it is checked, not trusted.

## Commit / PR

- Conventional-ish commit subjects (`feat:`, `fix:`, `docs:`, `rule:`).
- Small PRs. Describe the technique or TTP a change addresses if it's a rule.
- DCO sign-off requested (TBD by Rob — see Phase 0 §0.5 Q5).

## Code of conduct

Be decent to each other. Participation in this project is covered by
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — the short version: attack the
problem, not the person, and never use a security finding as leverage over
someone. Report concerns the way that file describes.

## Security

Found a vulnerability in agentids itself? Do not open a public issue. Report it
the way [`SECURITY.md`](SECURITY.md) describes — through GitHub private
vulnerability reporting — so it can be fixed before it is public.
