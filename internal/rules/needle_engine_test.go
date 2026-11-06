package rules

// needle_engine_test.go -- the compiled counterpart to `scripts/needleprobe.py`.
//
// That probe's header says, honestly:
//
//   "No Go toolchain on this host, so this is a reading of the source, not a compiled run."
//
// The toolchain returned 2026-11-01 (beat 740). Beat 780's `scripts/staleclaim.py` exists to catch
// exactly this kind of orphaned sentence, and beat 781's audit of the remaining toolchain claims
// found that between 776 and 781 `substringNeedle` was reached by no Go test at all -- the only
// mention of it in the test tree was inside a Python-mirror comment in `argv_semantics_test.go`.
// Its behaviour was observed ONLY through argv fixtures happening to route through it, which is
// coverage by accident rather than by assertion.
//
// `substringNeedle` decides whether ~20 exfil rules fire: every `*domain.com*` glob depends on the
// contains fallback. Beat 644's retraction established that this fallback is load-bearing on
// endpoints and pure loss on paths, and that finding rested on a mirror. This file makes the
// extractor's own contract a compiled fact.

import (
	"strings"
	"testing"
)

func TestSubstringNeedleExtraction(t *testing.T) {
	cases := []struct {
		glob string
		want string
		ok   bool
	}{
		// The three shapes the function documents.
		{"*slack.com*", "slack.com", true},
		{"*.ssh*", ".ssh", true},
		{"* /x", " /x", true}, // a space is not a wildcard: the core is the literal " /x"

		// Leading-only and trailing-only stars both strip.
		{"*id_rsa", "id_rsa", true},
		{"id_rsa*", "id_rsa", true},

		// No star at all: the whole glob is the needle. This is why a literal anchor still
		// substring-matches, which is a documented consequence -- not a glob match.
		{"slack.com", "slack.com", true},

		// Interior wildcards disqualify: a needle cannot express a wildcard.
		{"*sl*ck.com*", "", false},
		{"*a?b*", "", false},
		{"*[abc]*", "", false},

		// Degenerate: bare stars, empty.
		{"*", "", false},
		{"", "", false},
	}

	for _, c := range cases {
		got, ok := substringNeedle(c.glob)
		if ok != c.ok {
			t.Errorf("substringNeedle(%q) ok = %v, want %v", c.glob, ok, c.ok)
			continue
		}
		if ok && got != c.want {
			t.Errorf("substringNeedle(%q) = %q, want %q", c.glob, got, c.want)
		}
	}
}

// TestSubstringNeedleIsWhyDomainGlobsFire pins the load-bearing consequence: a `*domain.com*` glob
// fires on a full URL only because of this fallback. If the extractor ever stops offering a needle,
// ~20 exfil rules go silent while every existing test that checks "the rule is present in the pack"
// stays green -- which is the failure mode beat 644's retraction was about.
func TestSubstringNeedleIsWhyDomainGlobsFire(t *testing.T) {
	const url = "https://hooks.slack.com/services/T000/B000/XXXX"
	const glob = "*slack.com*"

	if !endpointFire(t, glob, url) {
		t.Fatalf("glob %q no longer fires on %q — the contains fallback is gone and roughly 20 "+
			"exfiltration rules are now silent. This is the beat 644 retraction's exact scenario.", glob, url)
	}

	// The needle is the ONLY reason: whole-glob, suffix, and base all fail on this input.
	needle, ok := substringNeedle(glob)
	if !ok {
		t.Fatalf("substringNeedle(%q) offered no needle; the rule could not have fired via it", glob)
	}
	if needle != "slack.com" {
		t.Fatalf("needle = %q, want \"slack.com\"", needle)
	}
	if !strings.Contains(url, needle) {
		t.Fatalf("the url %q does not contain the needle %q — this control case is wrong", url, needle)
	}
}
