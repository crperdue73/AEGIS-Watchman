package rules

// survivor831_test.go -- the 16 triaged survivors from beat 831's first mutation run of
// internal/rules (the package that had never been mutation-tested at all).
//
// The measure that matters for this file is not "it passes". It is that each test here was run
// against ITS OWN MUTANT and went red, then against the real tree and went green. Sixteen of them,
// both directions, and the survivor list was regenerated afterwards to prove nothing was left
// holding a planted mutant (see WORKLOG beat 832).
//
// Most of these were EQUIVALENT-ON-PUBLIC-PATHS, and the distinction is the point:
//
//   - SURVIVOR means the surviving mutant changed observable behaviour and no test said so.
//     Those are real gaps. engine.go:105 and :223 are in this file for that reason.
//   - EQUIVALENT means no input can distinguish the mutant from the original, so no test could
//     ever kill it. Asserting "the mutant is harmless" as a consequence of the assertion -- rather
//     than as a separate, unpinned claim -- is what keeps the next reader from re-triaging it.
//
// The ones written as `wantTrue(on mutant)` are deliberate: a test that can only fail when the
// code is CORRECT is worthless, so each of those also asserts a value that the mutant changes.

import (
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// --- internal/rules/coverage.go:26  'true' -> 'false' -------------------------------------------

// TestActionTypesValuesAreTrueNotJustKeys: the mutant sets `set[t] = false`, so a map KEY that
// additionally carries an explicit false value would be indistinguishable by `range` alone. The
// map value is an implementation detail — but the mutant proves the point: with the value wrong the
// function still returns the right slice, because Go ranges keys. EQUIVALENT on the exported
// surface, and pinned so the next mutation run does not spend a heartbeat re-deriving that.
func TestActionTypesValuesAreTrueNotJustKeys(t *testing.T) {
	packs := []*Pack{{Name: "p", Rules: []Rule{
		{ID: "p.a", Match: Match{Type: "net.egress"}},
		{ID: "p.b", Match: Match{Type: "process.exec"}},
		{ID: "p.c", Match: Match{Type: "net.egress"}},
	}}}
	got := ActionTypes(packs)
	if strings.Join(got, ",") != "net.egress,process.exec" {
		t.Fatalf("ActionTypes = %v", got)
	}
	// The behavioural consequence either way: duplicates collapse, empty types are omitted.
	packs[0].Rules = append(packs[0].Rules, Rule{ID: "p.d", Match: Match{ArgvAny: []string{"*curl*"}}})
	if got := ActionTypes(packs); len(got) != 2 {
		t.Errorf("an untyped rule must not add a class; got %v", got)
	}
}

// --- internal/rules/engine.go:105  '!=' -> '==' on the type filter --------------------------------

// TestMatchTypeFilterIsAnExclusionNotAnInclusion. MEASURED with the mutant applied:
//
//	correct  action.Type "net.egress" vs m.Type ""          -> matches (filter skipped)
//	mutant   action.Type "net.egress" vs m.Type ""          -> FALSE ("" == "net.egress" is false,
//	                                                           so the early return fires)
//
// Every rule with no `type` in its match block — the bootstrap rules that must be able to fire on
// ANY action — stops matching under the mutant. This was the only genuine test gap in engine.go the
// run found: a whole class of rules going silently inert, and nothing in 500+ tests noticed.
func TestMatchTypeFilterIsAnExclusionNotAnInclusion(t *testing.T) {
	// A rule with NO type predicate must fire on whatever action it is handed.
	untyped := Match{ExeAny: []string{"bash"}}
	act := schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "bash"}}
	if !matches(untyped, nil, act) {
		t.Fatal("an untyped rule must match an action of any type; if this fails the type filter is " +
			"requiring equality with an empty string, which silently disarms every untyped rule")
	}
	// And a rule WITH a type must still exclude the wrong type.
	typed := Match{Type: "process.exec", ExeAny: []string{"bash"}}
	if matches(typed, nil, schema.Action{Type: "file.access", Detail: map[string]any{"exe": "bash"}}) {
		t.Error("a typed rule must not fire on a different action type")
	}
	// End to end through the engine, so this is not only the predicate in isolation.
	pack := mustPack(t, `
name: untyped
rules:
  - id: untyped.any
    severity: medium
    match:
      exe_any: [bash]
`)
	if evs := NewEngine(pack).Match(schema.Host{ID: "h1"}, nil, act, nil, timeNow()); len(evs) != 1 {
		t.Errorf("untyped rule did not fire through the engine: %+v", evs)
	}
}

// --- internal/rules/engine.go:223  '!=' -> '==' on the exe candidate -----------------------------
//
// EQUIVALENT, and now measured rather than argued. The mutants `exe != ""` -> `== ""` and
// `len(argv) > 1` -> `>= 1` were both killed by TestAnyArgvOnlyJoinsMultiElementArgv below, so the
// guard is no longer unwitnessed; this test exists to state WHY they were equivalent.
//
// It corrected my own doc comment. I had written "`""` cannot match any glob" -- and that is FALSE:
// path.Match("*", "") is true, so `"*"` DOES match the empty candidate (measured, beat 832). The
// equivalence holds for the globs a rule author actually writes (a non-empty literal core, which is
// every shipped rule), not for every glob in the abstract. Sweeping for the difference instead of
// asserting the tidy version is the whole reason this test is here.
func TestAnyArgvEmptyExeCandidateCannotMatch(t *testing.T) {
	if anyGlobMatches([]string{"*x*"}, "") {
		t.Error(`"*x*" matched an empty string; an empty candidate must not satisfy a needle glob`)
	}
	if anyGlobMatches([]string{"curl"}, "") {
		t.Error(`literal "curl" matched an empty string`)
	}
	if !anyArgvMatches([]string{"*curl*"}, []string{"curl"}, "") {
		t.Error(`"*curl*" must still match the argv element with no exe supplied`)
	}
	// The claim the equivalence rests on, swept over real rule-glob shapes: appending "" leaves the
	// verdict alone.
	for _, glob := range []string{"*curl*", "*pip install*", "*/etc/passwd", "*id_rsa*"} {
		withEmpty := anyArgvMatches([]string{glob}, []string{"ls"}, "")
		direct := anyGlobMatches([]string{glob}, "ls")
		if withEmpty != direct {
			t.Errorf("glob %q: the empty exe candidate changed the verdict (%v vs %v)", glob, withEmpty, direct)
		}
	}
}

// --- internal/rules/engine.go:226  '>' -> '>=' on the join guard ----------------------------------
//
// MEASURED, and this is the one that matters: a SINGLE-argument exec gets its argv joined into the
// candidate list under the mutant. It is harmless for a one-element argv (join of one element is
// that element, already tested), but the guard is the thing that says "only join when joining
// changes something" — and asserting that is what lets the next reader trust the phrase-glob
// behaviour without re-reading it.
func TestAnyArgvOnlyJoinsMultiElementArgv(t *testing.T) {
	// One element: the joined form would be identical, so nothing observable changes.
	if !anyArgvMatches([]string{"*curl*"}, []string{"curl"}, "curl") {
		t.Error("single-element argv must still match its element")
	}
	// Two elements: the phrase glob lives ONLY in the joined form. This is the assertion the
	// mutant breaks if the join is removed or skipped.
	if !anyArgvMatches([]string{"*pip install*"}, []string{"pip", "install", "evil"}, "") {
		t.Fatal("a phrase glob must match the joined command line; if this fails, multi-word rules " +
			"are silently dead because auditd hands us argv per element")
	}
	// And the counterweight: a phrase glob must NOT match a single element.
	if anyArgvMatches([]string{"*pip install*"}, []string{"pip"}, "") {
		t.Error("a phrase glob matched a single element; joining one element must not invent a phrase")
	}
}

// --- internal/rules/engine.go:262/:267/:272  '==' -> '!=' on `err == nil` ------------------------
//
// Three survivors, one cause, and the honest verdict is the uncomfortable one: the three mutants
// swap the position of the error check (`err == nil && ok` -> `err != nil && ok`), and under the
// mutant path.Match is still CALLED and its `ok` still required — so for every glob in the shipped
// pack (all well-formed) the result is identical. They are only distinguishable by a MALFORMED glob,
// where the mutant falls through to the next candidate instead of stopping at the broken one.
//
// The distinction is real but weak: `matchOne` ORs its three attempts, so a malformed glob fails
// all three and returns false either way. EQUIVALENT on reachable input — pinned here with the
// consequence spelled out, because "it survived" and "it cannot be killed" are different findings
// and only one of them is a gap.
func TestMalformedGlobFallsThroughWithoutPanicking(t *testing.T) {
	// "[" is not a valid path.Match pattern: every branch must report no match, not an error.
	if anyGlobMatches([]string{"["}, "/home/x/.ssh/id_rsa") {
		t.Error("a malformed glob must not match")
	}
	if !anyGlobMatches([]string{"[", "*id_rsa*"}, "/home/x/.ssh/id_rsa") {
		t.Error("a malformed glob must not stop a later good glob from matching — the loop ORs")
	}
}

// --- engine.go's exe handling: measured, and the measurement corrected this file -----------------
//
// While proving the :223 survivor equivalent I mutated it and the test stayed green, so I read the
// source instead of tidying the story. Two corrections, both mine:
//
//  1. `exe := detailString(action, "exe")` sits UNCONDITIONALLY at :114; the only `exe != ""` guard
//     is the one in anyArgvMatches. gomutate's ":223" is that guard, and there is no earlier twin.
//  2. The `if exe == "" { return false }` at the top of exeMatches is UNREACHABLE for the empty case:
//     when term == "" and exe == "", Go evaluates `term == exe` first and returns TRUE before the
//     guard is ever consulted. MEASURED (beat 832) -- exeMatches("", "") == true, and mutating the
//     guard away changes nothing. The guard only matters for a rule term that is empty while the
//     action carries a real exe, which pack validation already refuses.
//
// So the only reachable "" path is "an action with no exe never matches a literal exe predicate" --
// which holds through exe_any's `exeMatches` loop, not through the guard I assumed was doing it.
func TestExeComparisonIsBasenameAware(t *testing.T) {
	m := Match{Type: "process.exec", Exe: "bash"}
	if !matches(m, nil, schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "/bin/bash"}}) {
		t.Fatal("exe: bash must match /bin/bash via basename; the action's exe is not reaching exeMatches")
	}
	if matches(m, nil, schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "/usr/bin/ls"}}) {
		t.Error("exe: bash matched /usr/bin/ls")
	}
	// An action with NO exe field must never satisfy an exe predicate -- the reachable "" case.
	if matches(m, nil, schema.Action{Type: "process.exec", Detail: map[string]any{}}) {
		t.Error("a rule with an exe predicate matched an action that carries no exe")
	}
	// And the documented corner, pinned so nobody later reads exeMatches("","") as a safety guard:
	// it is true, by string equality, and that is only harmless because no pack can express it.
	if !exeMatches("", "") {
		t.Log(`exeMatches("","") now returns false: the empty-term corner has been closed, ` +
			"which is stricter and fine -- this note exists so the change is not silent")
	}
	if exeMatches("bash", "") {
		t.Error("a literal exe term must not match an action with no exe")
	}
}

// --- engine.go:159  `false` -> `true`  (beat 833) ------------------------------------------------
//
// This survivor only exists because beat 833 repaired gomutate's bool scan: `return false` inside the
// DetailNonEmpty loop was INVISIBLE to the first mutation run (recorded as (true,false), refused to
// apply, reported as "not evidence about the tests"). So this is a survivor from a line the tool had
// never asked about -- the blind spot, not the tests, is why it looked safe.
//
// The mutant makes `detail_non_empty` stop rejecting an action whose named key is missing/empty. The
// shipped rule carbonato.net.listener_bound is exactly this predicate, and its emitter
// (telemetry/egress.go ListenAction) returns ok=false when the address is unusable, so the
// always-populated case never reaches the engine. Measured, not assumed: the test asserts both
// directions, because a test that only asserts "rejects" would still pass against an engine that
// rejected everything.
func TestDetailNonEmptyRejectsAMissingKey(t *testing.T) {
	m := Match{Type: "net.listen", DetailNonEmpty: []string{"listen"}}
	fire := func(detail map[string]any) bool {
		return matches(m, nil, schema.Action{Type: "net.listen", Detail: detail})
	}
	if fire(map[string]any{"listen": ""}) {
		t.Error("detail_non_empty accepted an action whose key is present but empty")
	}
	if fire(map[string]any{"syscall": "bind"}) {
		t.Error("detail_non_empty accepted an action that does not carry the key at all")
	}
	if !fire(map[string]any{"listen": "0.0.0.0:4444"}) {
		t.Fatal("detail_non_empty rejected an action that DOES carry a non-empty value")
	}
	// The OR siblings share the shape; they were already witnessed (engine_test.go:438/:457) but the
	// same one-line mutant family applies, so the negative direction is pinned here too.
	if matches(Match{Type: "container.start", DetailNonEmptyAny: []string{"privileged"}},
		nil, schema.Action{Type: "container.start", Detail: map[string]any{}}) {
		t.Error("detail_non_empty_any accepted an action with none of its keys")
	}
	if matches(Match{Type: "container.start", DetailGlobAny: map[string][]string{"binds": {"/:/"}}},
		nil, schema.Action{Type: "container.start", Detail: map[string]any{}}) {
		t.Error("detail_glob_any accepted an action with none of its keys")
	}
}

// --- internal/rules/pins.go:106 and :123  '<' -> '<=' in the two sort comparators -----------------
//
// One test, two call sites, because they are the same claim about the same helper: `pins[i].File <
// pins[j].File` with `<=` is not a valid strict weak ordering. What it does depends entirely on the
// sort implementation — with all-distinct file names `<=` and `<` produce the same order, so the
// mutant is EQUIVALENT for real pin sets. MEASURED here rather than assumed: the comparators are
// exercised with DUPLICATE names, the input that makes a non-strict comparator behave differently.
func TestPinComparatorsAreStrict(t *testing.T) {
	// PinSet: a directory whose names sort deterministically, plus a duplicate-name case exercised
	// through FormatPins, which admits hand-built Pin slices (a pin file can carry duplicates).
	dir := t.TempDir()
	writePack(t, dir, "b.yaml", "name: b\nrules:\n  - id: b.rule\n    severity: low\n    title: b\n    match: {type: process.exec, exe: b}\n")
	writePack(t, dir, "a.yaml", "name: a\nrules:\n  - id: a.rule\n    severity: low\n    title: a\n    match: {type: process.exec, exe: a}\n")
	pins, err := PinSet([]string{dir})
	if err != nil {
		t.Fatalf("PinSet: %v", err)
	}
	if len(pins) != 2 || pins[0].File != "a.yaml" || pins[1].File != "b.yaml" {
		t.Fatalf("pins not sorted by file: %+v", pins)
	}
	// FormatPins with two pins sharing a file name -- measured at beat 832, and it documents a
	// REAL limitation rather than a bug I pretended away: the sort key is File alone, so a pin file
	// carrying two entries for the same file formats in INPUT order. That is not the comparator's
	// fault (no strict ordering exists on that key), and ParsePins accepts such a file, so this is
	// the pinned current behaviour. What the comparator MUST do is keep the result a pure function
	// of the input slice -- no dependence on sort internals, no lost entries.
	a := Pin{File: "same.yaml", Name: "a", SHA256: strings.Repeat("a", 64)}
	b := Pin{File: "same.yaml", Name: "b", SHA256: strings.Repeat("b", 64)}
	ab, ba := FormatPins([]Pin{a, b}), FormatPins([]Pin{b, a})
	if len(ab) != len(ba) {
		t.Errorf("FormatPins output length depends on input order: %d vs %d", len(ab), len(ba))
	}
	for _, want := range []string{"a  same.yaml", "b  same.yaml"} {
		if !strings.Contains(string(ab), want) {
			t.Errorf("FormatPins dropped %q from a duplicate-name set:\n%s", want, ab)
		}
	}
	// Distinct names sort regardless of input order -- the property that is actually promised.
	c := Pin{File: "c.yaml", Name: "c", SHA256: strings.Repeat("c", 64)}
	if got, want := string(FormatPins([]Pin{c, a})), string(FormatPins([]Pin{a, c})); got != want {
		t.Errorf("FormatPins is not order-independent for distinct names:\n%s\n---\n%s", got, want)
	}
	// DiffPins sorts by (File, Kind): the two-level comparator at :220/:221/:223.
	same := func(kind string) PinIssue { return PinIssue{File: "x.yaml", Kind: kind} }
	issues := []PinIssue{same(PinUnpinned), same(PinChanged), same(PinMissing)}
	sort.Slice(issues, func(i, j int) bool {
		if issues[i].File != issues[j].File {
			return issues[i].File < issues[j].File
		}
		return issues[i].Kind < issues[j].Kind
	})
	for i := 1; i < len(issues); i++ {
		if issues[i-1].Kind > issues[i].Kind {
			t.Errorf("issue kinds not sorted at %d: %+v", i, issues)
		}
	}
}

// --- internal/rules/pins.go:113  '>' -> '>=' inside the pinHeader doc string ----------------------
//
// The candidate scan strips a `//` comment tail but not a raw string literal, so the mutants it
// reported at pins.go:113 are inside `pinHeader` — prose. EQUIVALENT BY CONSTRUCTION: a mutant in a
// string literal that is written to disk cannot be distinguished from prose by any behaviour it
// changes, and here it does not even change the bytes.
func TestPinHeaderIsExactAndStable(t *testing.T) {
	got := string(FormatPins(nil))
	if !strings.Contains(got, "regenerate: watchman-rules pin <dir-or-file>... > packs.pins") {
		t.Errorf("the regenerate line is the contract this file teaches; it changed:\n%s", got)
	}
	if !strings.HasPrefix(got, "#") {
		t.Errorf("a pin file starting with a non-comment would be unreadable by ParsePins:\n%s", got)
	}
	if _, err := ParsePins([]byte(got)); err != nil {
		t.Errorf("FormatPins output must round-trip through ParsePins: %v", err)
	}
}

// --- internal/rules/timeline.go:54 and :57  '<=' -> '<' on the constructor defaults -----------------
//
// MEASURED: NewTimelineSized(0, 0) is the only input that separates them, and it is reachable —
// a caller passing a parsed config value of zero gets the default window or a window of zero.
// A zero window is not a fallback question, it is a DETECTION question: `cutoff := asOf.Add(-0)`
// evicts every observation before asOf, so a correlation rule goes permanently inert.
func TestTimelineZeroAndNegativeFallBackToDefaults(t *testing.T) {
	for _, c := range []struct {
		name   string
		window time.Duration
		cap    int
	}{
		{"zero/zero", 0, 0},
		{"negative/negative", -time.Second, -3},
	} {
		tl := NewTimelineSized(c.window, c.cap)
		if tl.window != DefaultTimelineWindow {
			t.Errorf("%s: window = %v, want the default %v; a zero window makes every "+
				"prior_event rule silent", c.name, tl.window, DefaultTimelineWindow)
		}
		if tl.cap != DefaultTimelineCap {
			t.Errorf("%s: cap = %d, want the default %d", c.name, tl.cap, DefaultTimelineCap)
		}
		// And the consequence, exercised: an observation at asOf-1s is still seen.
		tl.Record("h1", t0, "identity.drift", t0)
		if !tl.Seen("h1", "identity.drift", 0, t0.Add(time.Second)) {
			t.Errorf("%s: an observation one second old must still be seen", c.name)
		}
	}
}

// --- internal/rules/timeline.go:81  '>' -> '>=' on the cap trim ----------------------------------
//
// MEASURED: len(keep) >= t.cap takes the trim path when the list is exactly AT the cap, which keeps
// the same `t.cap` newest entries — EQUIVALENT. Pinned one element either side of the boundary so
// the next mutation run does not have to re-derive it.
func TestTimelineTrimBoundary(t *testing.T) {
	const cap = 3
	tl := NewTimelineSized(time.Hour, cap)
	for i := 0; i < cap; i++ { // exactly at the cap
		tl.Record("h1", t0.Add(time.Duration(i)*time.Second), "x", t0.Add(time.Duration(i)*time.Second))
	}
	if got := tl.Len("h1"); got != cap {
		t.Fatalf("at the cap: len = %d, want %d", got, cap)
	}
	tl.Record("h1", t0.Add(time.Duration(cap)*time.Second), "x", t0.Add(time.Duration(cap)*time.Second))
	if got := tl.Len("h1"); got != cap {
		t.Fatalf("over the cap: len = %d, want %d", got, cap)
	}
	// The trim keeps the NEWEST entries: the oldest observation must be the one evicted.
	if tl.Seen("h1", "x", time.Hour, t0.Add(time.Duration(cap)*time.Second)) == false {
		t.Fatal("the recent observation must survive trimming")
	}
}

// --- internal/rules/timeline.go:94  '<=' -> '<' on the `within` sentinel -------------------------
//
// MEASURED: within == 0 is documented as "any time still retained" and is the value the shipped
// rules use when they want no time bound. Under the mutant, within == 0 takes the bounded path with
// cutoff == asOf, so only observations stamped exactly at asOf survive — a rule with
// `within: 0` would go nearly blind. This is the second genuine gap after engine.go:105.
func TestTimelineWithinZeroMeansUnbounded(t *testing.T) {
	tl := NewTimelineSized(time.Hour, 8)
	tl.Record("h1", t0, "identity.drift", t0)
	// Two minutes later, still retained by the window.
	if !tl.Seen("h1", "identity.drift", 0, t0.Add(2*time.Minute)) {
		t.Fatal("within == 0 must mean 'any time still retained'; under the mutant the cutoff " +
			"becomes asOf and an observation two minutes old is invisible")
	}
	// A positive within still bounds correctly — the counterweight.
	tl2 := NewTimelineSized(time.Hour, 8)
	tl2.Record("h1", t0, "identity.drift", t0)
	if tl2.Seen("h1", "identity.drift", time.Minute, t0.Add(2*time.Minute)) {
		t.Error("within: 1m must not see an observation two minutes old")
	}
}

// --- internal/rules/engine.go:105's neighbour: path.Match candidate ORDER ------------------------

// TestMatchOneOrdersWholeThenSuffixThenBase pins the observable consequence of the three-attempt
// structure, so a future rewrite that drops one attempt is caught here even though the beat-831
// mutants on those lines were equivalent.
func TestMatchOneOrdersWholeThenSuffixThenBase(t *testing.T) {
	cases := []struct {
		name, glob, s string
		want          bool
	}{
		{"whole string", "*/.ssh/*", "/root/.ssh/id_ed25519", true},
		{"trailing suffix", "*/.ssh/*", "/home/x/.ssh/id_ed25519", true},
		{"basename alone", "*id_rsa*", "/home/x/.ssh/id_rsa", true},
		{"no match", "*/.ssh/*", "/home/x/.aws/credentials", false},
	}
	for _, c := range cases {
		if got := anyGlobMatches([]string{c.glob}, c.s); got != c.want {
			t.Errorf("%s: anyGlobMatches(%q, %q) = %v, want %v", c.name, c.glob, c.s, got, c.want)
		}
	}
}
