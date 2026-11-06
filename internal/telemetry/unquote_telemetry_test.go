package telemetry

// decodeHexEscapes / unquote had NO test until this file (beat 787). They are the functions that
// turn auditd's raw record fields back into the argv the rule engine matches on. A defect here does
// not look like a crash; it looks like a rule that quietly fails to fire, or fires on prose.
//
// I checked the boundary before writing these (that `i+3 < len(s)` is not an off-by-one) and it is
// correct: when the escape occupies the final four bytes, i = len-4 and i+3 = len-1 < len. No
// defect found. The finding is therefore not a bug -- it is that nobody was asking.

import "testing"

func TestDecodeHexEscapes(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
		why  string
	}{
		{"leading escape", `\x41BC`, "ABC", "escape at the start"},
		{"trailing escape", `ab\x41`, "abA", "escape at the very end -- the i+3 boundary"},
		{"escape only", `\x41`, "A", "shortest string that can hold an escape"},
		{"slash", `\x2f`, "/", "the path separator, the case that makes a path rule match"},
		{"embedded nul", "a\\x00b", "a\x00b", "control byte survives into argv"},
		{"two escapes", `\x41\x42`, "AB", "index advance must skip exactly one escape, not two"},

		{"no escape", "plain", "plain", "nothing to do"},
		{"lone backslash x", `x41`, "x41", "without a backslash it is literal text"},
		{"invalid hex", `\xz41`, `\xz41`, "unparseable -- must pass through untouched, not drop the byte"},
		{"truncated escape", `ab\x4`, `ab\x4`, "too short to be a hex pair; must not panic or eat the tail"},
		{"bare backslash", `a\`, `a\`, "trailing backslash at end of input"},
		{"empty", "", "", "empty in, empty out"},
		{"uppercase hex", `\x4A`, "J", "ParseUint is case-insensitive; pin it"},
	}
	for _, c := range cases {
		if got := decodeHexEscapes(c.in); got != c.want {
			t.Errorf("%s: decodeHexEscapes(%q) = %q, want %q (%s)", c.name, c.in, got, c.want, c.why)
		}
	}
}

// A rule-matching consequence, not a unit fact: the reason this function exists is that auditd
// hands back `\/etc\/passwd`-shaped bytes and a path rule can never match the escaped form.
func TestDecodeHexEscapesMakesAPathMatchable(t *testing.T) {
	escaped := `\x2fetc\x2fpasswd`
	got := decodeHexEscapes(escaped)
	if got != "/etc/passwd" {
		t.Fatalf("decodeHexEscapes(%q) = %q, want %q -- an escaped path is invisible to every rule "+
			"in the pack", escaped, got, "/etc/passwd")
	}
}

func TestUnquote(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{"quotes stripped", `"/etc/passwd"`, "/etc/passwd"},
		{"quotes plus escape", `"\x2fetc"`, "/etc"},
		{"unquoted passthrough", `/etc/passwd`, "/etc/passwd"},
		{"single quote char is not a quote pair", `"`, `"`},
		{"empty pair", `""`, ""},
		{"unquoted but escaped", `\x2fetc`, "/etc"},
		{"quotes on one side only", `"/etc`, `"/etc`},
		{"empty", "", ""},
	}
	for _, c := range cases {
		if got := unquote(c.in); got != c.want {
			t.Errorf("%s: unquote(%q) = %q, want %q", c.name, c.in, got, c.want)
		}
	}
}

// unquote must strip only ONE layer -- a nested-quoted argv is real (a shell -c payload), and
// stripping repeatedly would corrupt it. Pinned so a future "improvement" has to argue with a test.
func TestUnquoteStripsOnlyOneLayer(t *testing.T) {
	if got, want := unquote(`""x""`), `"x"`; got != want {
		t.Errorf("unquote(%q) = %q, want %q -- only one layer", `""x""`, got, want)
	}
}
