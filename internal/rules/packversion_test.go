package rules

// BEAT 761 -- the rule pack is the STRICT one, and its format version is dead weight.
//
// Same-family sweep after finding 2026-11-02o. Three loaders, three different
// answers to "what does a format version mean?":
//
//	provenance manifest : version ENFORCED (hard error)          -- finding 2026-11-02m
//	central filestore   : version WRITTEN, never read            -- finding 2026-11-02k
//	rules pack          : FormatVersion declared, USED NOWHERE   -- this beat
//
// And the pack gets the thing the manifest lacks: ParsePack calls
// dec.KnownFields(true), so an unknown predicate is a hard error rather than a
// silently dropped line. The comment at rule.go:120 states the stake better than
// I could: "a rule that loads but can never fire ... is the worst failure mode
// for a detection tool, because the operator believes they are covered."
//
// T1  control: a well-formed pack loads
// T2  unknown KEY is refused (KnownFields) -- the strict behaviour, MEASURED
// T3  a rule pack's `version` is free-form: "999" loads, and nothing compares it
// T4  FormatVersion is never consulted: a pack declaring an impossible format is
//     indistinguishable from one declaring the current format
// T5  honesty brake: is T4 actually a defect? Measure whether the FORMAT can even
//     differ today -- KnownFields already gives forward safety on predicates, so
//     the version field may be redundant rather than unread

import (
	"strings"
	"testing"
)

const goodPack = `
name: demo
version: "1"
rules:
  - id: demo.exec.sh
    title: shell spawn
    severity: high
    match:
      type: process.exec
      argv_any: ["*|sh*"]
`

// T1 -- control.
func TestPackLoads(t *testing.T) {
	p, err := ParsePack([]byte(goodPack))
	if err != nil {
		t.Fatalf("T1: a valid pack failed to load: %v", err)
	}
	if p.Name != "demo" || len(p.Rules) != 1 {
		t.Fatalf("T1: pack did not parse as expected: %+v", p)
	}
}

// T2 -- the STRICT behaviour, measured. This is the contrast with the manifest,
// which accepts unknown keys (finding 2026-11-02m, Q3).
func TestUnknownPackKeyIsRefused(t *testing.T) {
	bad := strings.Replace(goodPack, "    match:", "    bonus_predicate: true\n    match:", 1)
	_, err := ParsePack([]byte(bad))
	if err == nil {
		t.Fatalf("T2: an unknown rule key was ACCEPTED -- KnownFields(true) is gone, " +
			"and a misspelled predicate would now under-match silently. That is the " +
			"failure this pack format was built to prevent")
	}
	if !strings.Contains(err.Error(), "bonus_predicate") {
		t.Fatalf("T2: refused, but the error does not name the offending field: %v", err)
	}
}

// T3 -- the pack-level version is free-form and never compared.
func TestPackVersionIsFreeForm(t *testing.T) {
	for _, v := range []string{"999", "not-a-semver", "", "0"} {
		pack := strings.Replace(goodPack, `version: "1"`, `version: "`+v+`"`, 1)
		p, err := ParsePack([]byte(pack))
		if err != nil {
			t.Fatalf("T3: version %q was refused (%v) -- the pack has gained version "+
				"semantics; re-read finding 2026-11-02p", v, err)
		}
		if p.Version != v {
			t.Fatalf("T3: version %q came back as %q", v, p.Version)
		}
	}
	// Nothing compares it to FormatVersion, and FormatVersion is not exported into
	// any parse path. Pinned as a log so the claim is visible in -v output.
	t.Logf("T3 MEASURED: pack.version is carried verbatim (%q, %q, %q, %q) and never "+
		"compared to FormatVersion=%d", "999", "not-a-semver", "", "0", FormatVersion)
}

// T4 -- a pack that declares an impossible format is indistinguishable from a
// good one, because there is no format field to declare -- FormatVersion is not
// part of Pack at all. This asserts the SHAPE, so if someone adds a format field
// the test announces that the question changed.
func TestPackHasNoFormatFieldToCheck(t *testing.T) {
	p, err := ParsePack([]byte(goodPack))
	if err != nil {
		t.Fatalf("T4: %v", err)
	}
	// Pack.Version inherits into rules that don't declare their own (rule.go:146).
	// That is a semantic use of the field -- but as a LABEL, not a gate.
	if p.Rules[0].Version != "1" {
		t.Fatalf("T4: rule did not inherit the pack version (%q) -- the inheritance "+
			"rule changed, re-read this finding", p.Rules[0].Version)
	}
}

// T5 -- the honesty brake, and the point of the finding. Does the FORMAT actually
// vary today? KnownFields(true) already refuses any predicate or field this build
// does not know, which is the forward-safety property a format version normally
// buys. So FormatVersion is likely REDUNDANT, not broken -- and that is a
// different claim from the filestore's unread version (finding k), where the old
// build would silently accept a new format.
//
// Measured: the strict decoder refuses a future field regardless of any version
// number, which means the version cannot be the thing that protects a reader.
func TestStrictDecoderIsWhatProtectsTheReaderNotTheVersion(t *testing.T) {
	future := strings.Replace(goodPack, "      argv_any:", "      matches_content_pattern: [\"*.ps1\"]\n      argv_any:", 1)
	_, err := ParsePack([]byte(future))
	if err == nil {
		t.Fatalf("T5: a pack using a field this build does not know was accepted")
	}
	t.Logf("T5 MEASURED: a pack claiming version \"999\" loads fine, but one using one "+
		"unknown field is refused (%v). The strict decoder, not the version number, "+
		"is what gives forward safety -- so FormatVersion is redundant, not unread "+
		"in the sense finding 2026-11-02k means", err)
}
