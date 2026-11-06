# FINDING 2026-11-03c — the default-build contract test asserted nothing on the path that mattered

**Status:** MEASURED · **FIXED at beat 775** · default build PASS, `-tags ebpf` SKIP (printed), `-race` clean
**Instrument:** `internal/telemetry/ebpf_test.go` (`TestStubReturnsUnsupported`)
**Follows:** FINDING-2026-11-03b (beat 774 audited the *disclosure* of skips; this audits the *correctness* of one)

## What was measured

`TestStubReturnsUnsupported` is the test that holds the default-build contract — decision D3 in
`docs/R2-EBPF-DESIGN.md`: *a stock checkout must build on any machine, and the stub must refuse with
`ErrEBPFUnsupported` so the collector degrades to auditd.*

**Measured: mutate `ebpf_stub.go` to `return nil, nil`** — a stub that attaches nothing and reports no error, i.e. the
degradation contract failing **open** — and the test still printed **PASS**.

Reading the old body explains it:

```go
src, err := NewEBPFSource(EBPFConfig{Enabled: true}, nil)
if err == nil {
    if src != nil {
        t.Skip("eBPF implemented; stub contract test no longer applies")
    }
}
if err != nil && !errors.Is(err, ErrEBPFUnsupported) && !strings.Contains(err.Error(), "not yet implemented") {
    t.Errorf("unexpected error: %v", err)
}
```

Three separate ways to assert nothing:

1. **`err == nil` and `src == nil`** — falls straight through both `if`s. No verdict. **PASS.** This is precisely the
   `(nil, nil)` mutant. The one behaviour the test exists to forbid was the one behaviour it could not see.
2. **The `t.Skip` was unreachable.** It sat inside a branch that had already established `err == nil` and checked
   `src != nil` — but if eBPF were genuinely implemented, the tagged build would return a `Source` *with* whatever error
   it has, and the enclosing `if err == nil` would not have been entered. A skip nobody can reach is a comment with
   syntax.
3. **`strings.Contains(err.Error(), "not yet implemented")`** — matching on error *text* made any future reword of the
   tagged implementation's message turn into a test failure, and made the sentinel check optional in practice.

## The fix

Assert the contract instead of describing it. In the default build, an enabled config **must** produce:

- a **non-nil error** (`src` must not be the thing that carries the refusal),
- with `errors.Is(err, ErrEBPFUnsupported)` — the sentinel, not a string,
- **and** `src == nil` (refusing must not also hand back a Source).

Every other outcome is a failure with a sentence explaining what a wrong pass would have meant.

**Both mutants now caught by name:**

- stub → `return nil, nil` ⇒ `FAIL … returned a nil error (src=<nil>) … the caller silently attaches nothing and
  believes it succeeded.`
- stub → `return &stubSource{}, ErrEBPFUnsupported` ⇒ `FAIL … a refusing stub must not also return a Source, got &{[] <nil> 0}`

`ebpf_stub.go` restored byte-identical, md5 `37a448b97d30b45b34dceaaa2d452b32`.

## Two self-corrections, both caught by measuring instead of assuming

**1. I wrote a skip keyed on `src != nil` and it broke the tagged build.** `go test -tags ebpf` went red with
*"want errors.Is(err, ErrEBPFUnsupported), got: ebpf source not yet implemented (R2.2)"*. Reading
`ebpf_linux.go:21-26` — the thing I should have done before writing the discriminator — the tagged implementation returns
**`(nil, error)`**: a nil `Source`. So `src != nil` never fired. The honest discriminator is the **error identity**: only
the stub returns `ErrEBPFUnsupported`. After the fix, `-tags ebpf` **skips and prints**, default build **asserts**.

**2. My own skip auditor caught my new skip — correctly.** F2 reported `internal/telemetry/ebpf_test.go line 79` as an
unmarked skip. The reason was there and honest ("the default-build stub contract does not apply"), but the classifier's
vocabulary did not include *"does not apply"*. Fixed by **widening the marker list**, which is the safe direction — it can
only lower the count — with a comment recording why. The pin at 0 still stops a genuinely silent skip.

That second one is the guard working as designed: I added a skip at 3:50 AM, and before I could finish the beat the
instrument from an hour ago made me justify it.

## Why this beats the last three

767, 770, 772 were failures that looked like PASS *in production pages*. 774 was a test that stopped checking. **775 is a
test that checked the wrong branch of its own body** — and unlike the others, this one had a comment claiming exactly the
behaviour it failed to assert: *"This test asserts the default-build contract."* It asserted it on two of four paths.

## Not claimed

- The tagged-build path is **skipped, not exercised** — R2.2 is unimplemented, so there is no real eBPF source to test.
  When R2.2 lands, the skip's message says the contract no longer applies; promote it to a real test then.
- I did not audit the other five skips for correctness. `argv_semantics_test.go` is next candidate.
- No production behaviour changed (`ebpf_stub.go` is restored). No decision needed from Dad.
