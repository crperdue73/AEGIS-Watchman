package rules

import (
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

func TestActionTypes(t *testing.T) {
	packs := []*Pack{{Name: "p", Rules: []Rule{
		{ID: "p.a", Match: Match{Type: "net.egress"}},
		{ID: "p.b", Match: Match{Type: "process.exec"}},
		{ID: "p.c", Match: Match{Type: "net.egress"}},          // duplicate
		{ID: "p.d", Match: Match{ArgvAny: []string{"*curl*"}}}, // no type
	}}}
	got := ActionTypes(packs)
	want := []string{"net.egress", "process.exec"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ActionTypes = %v, want %v", got, want)
	}
}

func TestInertRulesBasics(t *testing.T) {
	packs := []*Pack{{Name: "p", Rules: []Rule{
		{ID: "p.exec", Match: Match{Type: "process.exec"}},
		{ID: "p.access", Match: Match{Type: "file.access"}},
		{ID: "p.any", Match: Match{ArgvAny: []string{"*x*"}}}, // type-less: never inert
	}}}

	// Only process.exec available -> the file.access rule is inert; the type-less
	// rule is not.
	got := ids(InertRules(packs, map[string]bool{"process.exec": true}))
	if want := []string{"p.access"}; !reflect.DeepEqual(got, want) {
		t.Errorf("inert = %v, want %v", got, want)
	}

	// Both available -> nothing inert.
	if got := InertRules(packs, map[string]bool{"process.exec": true, "file.access": true}); len(got) != 0 {
		t.Errorf("expected no inert rules, got %v", ids(got))
	}
}

// TestShippedPackCoverageOnStockBuild pins the coverage story of the shipped
// pack against the source combinations a host can actually have. It exists so
// that adding a rule keyed on an action class no shipped source emits fails
// here, loudly, instead of shipping as silent non-coverage.
func TestShippedPackCoverageOnStockBuild(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatalf("load shipped pack: %v", err)
	}
	packs := []*Pack{p}

	stock := stockAvailable()
	withDocker := map[string]bool{"container.start": true}
	withEBPF := map[string]bool{"file.access": true}

	fileAccessRules := []string{
		"carbonato.secret.read_order_follows_constraint",
		"carbonato.secret.read_after_shell",
		"carbonato.secret.read_model_key",
	}
	containerRules := []string{
		"carbonato.container.privileged_start",
		"carbonato.container.host_root_mount",
		"carbonato.container.after_identity_change",
	}

	cases := []struct {
		name  string
		avail map[string]bool
		want  []string
	}{
		{"stock (auditd + scope)", stock, append(append([]string{}, fileAccessRules...), containerRules...)},
		{"stock + docker", merge(stock, withDocker), fileAccessRules},
		{"stock + ebpf", merge(stock, withEBPF), containerRules},
		{"stock + both", merge(merge(stock, withDocker), withEBPF), nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			inert := ids(InertRules(packs, tc.avail))
			want := append([]string{}, tc.want...)
			sort.Strings(want)
			if !reflect.DeepEqual(inert, want) {
				t.Errorf("inert = %v, want %v", inert, want)
			}
		})
	}

	// The stock-build headline the docs now state: 6 of 20 rules are inert,
	// and they include 5 of the pack's 6 critical rules.
	inert := InertRules(packs, stock)
	if len(inert) != 6 {
		t.Errorf("stock-build inert count = %d, want 6", len(inert))
	}
	crit := 0
	for _, r := range inert {
		if r.Severity == "critical" {
			crit++
		}
	}
	if crit != 5 {
		t.Errorf("stock-build inert critical = %d, want 5", crit)
	}
}

// stockAvailable is the set of action classes a stock collector emits: auditd
// (when audit_log is set) plus the default-on scope.change shim. eBPF
// (file.access) and docker (container.start) are both opt-in and off by default.
// Shared with the docs-agreement guard so the prose and the code use one model.
// stockAvailable is the class set for a STOCK BUILD with auditd plus the scope adapter attached --
// NOT an auditd-only host. It includes scope.change, which behavior.go:245 only sets when
// scopeAttached. The distinction is not cosmetic: this map yields 6 inert / 14 live, while a true
// auditd-only host yields 7 inert / 13 live (carbonato.tool.scope_new is the difference). That is
// FINDING-2026-10-26f, and it is the case where five of six critical rules go dark.
func stockAvailable() map[string]bool {
	return map[string]bool{
		"process.exec": true, "net.egress": true, "net.listen": true,
		"file.write": true, "scope.change": true,
	}
}

// auditdOnlyAvailable mirrors behavior.go:245 exactly: the four classes auditd carries, and nothing
// else. Use this -- not stockAvailable -- for any claim about degraded operation.
func auditdOnlyAvailable() map[string]bool {
	return map[string]bool{
		"process.exec": true, "net.egress": true, "net.listen": true, "file.write": true,
	}
}

func ids(rs []Rule) []string {
	out := make([]string, 0, len(rs))
	for _, r := range rs {
		out = append(out, r.ID)
	}
	sort.Strings(out)
	return out
}

func merge(a, b map[string]bool) map[string]bool {
	out := make(map[string]bool, len(a)+len(b))
	for k := range a {
		out[k] = true
	}
	for k := range b {
		out[k] = true
	}
	return out
}

// TestInertRulesCannotSeeInsideAnAvailableClass states the instrument's limit as executable fact.
//
// InertRules compares each rule's Match.Type against the set of action classes a host can emit. That
// answers exactly one question: can this class reach the engine? It says nothing about whether the
// rule's match VALUES accept what the emitter actually produces for that class.
//
// This is not hypothetical. Everything found in Phase 0 during beats 599-605 lives inside an available
// class:
//
//   - persist.file_write (class always available): every path_glob_any ends in "/*" or a leaf filename,
//     so the bare directory path auditd reports for `rm -rf /etc/cron.d` matches nothing.   [26e]
//   - exfil.egress_after_identity_change (class available): 30 of 32 endpoint_glob_any values cannot
//     match the "host:port" / "host ip:port" strings egress.go emits.                      [26c]
//   - dump.memory_read / docker.binds / processes.any: over-broad globs matching far more than
//     intended, e.g. *:/host* catching /hostdata, /hostile, /host-backup.                 [26b]
//
// On an auditd-only host the pack loses five of its six critical rules, and InertRules reports that
// correctly -- but for the rules it keeps, it has nothing to say. A clean InertRules result is not a
// clean bill of health, and nothing in the output says so.
//
// The contract this test locks: coverage.go is content-blind BY CONSTRUCTION. If someone extends
// InertRules to inspect match values, this test fails and the docs must move with it.
func TestInertRulesCannotSeeInsideAnAvailableClass(t *testing.T) {
	// A rule whose class IS available but whose only match value can never match anything the
	// emitter produces. InertRules must still call it live -- that is the blind spot.
	packs := []*Pack{{Name: "p", Rules: []Rule{
		{
			ID:    "p.available_but_unmatchable",
			Match: Match{Type: "file.write", PathGlobAny: []string{"/this/path/is/never/emitted/*"}},
		},
		{
			ID:    "p.available_but_empty_globs",
			Match: Match{Type: "file.write", PathGlobAny: nil},
		},
	}}}
	available := map[string]bool{"file.write": true}

	if got := ids(InertRules(packs, available)); len(got) != 0 {
		t.Errorf("InertRules reported inert = %v; it is supposed to be content-blind. If it now "+
			"inspects match values, update the doc comment on coverage.go and FINDING-2026-10-26d.", got)
	}

	// The contrast that makes the blind spot concrete: remove the class and it IS caught.
	if got := ids(InertRules(packs, map[string]bool{})); len(got) != 2 {
		t.Errorf("without the class both rules should be inert, got %v", got)
	}

	// And the shipped pack must show the same blind spot on a stock auditd-only host: the rules
	// flagged in Phase 0 are NOT among the inert set, because their classes are available.
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatalf("load shipped pack: %v", err)
	}
	// Measured against a TRUE auditd-only host (behavior.go:245), not stockAvailable -- those differ
	// by carbonato.tool.scope_new, and 26f's numbers are auditd-only numbers.
	inert := map[string]bool{}
	for _, r := range InertRules([]*Pack{p}, auditdOnlyAvailable()) {
		inert[r.ID] = true
	}
	for _, id := range []string{
		"carbonato.persist.file_write",
		// The one critical rule that survives on an auditd-only host -- and only fires against two
		// brands on three IP ranges. Live, and nearly blind. [26c, 26f]
		"carbonato.exfil.egress_after_identity_change",
	} {
		if inert[id] {
			t.Errorf("%s is inert on a stock auditd-only host -- that contradicts FINDING-2026-10-26f, "+
				"which measured it as live-but-blind. Re-measure before changing this test.", id)
		}
	}
}

// TestStockAndAuditdOnlyAreDifferentHosts pins the distinction that produced two different numbers for
// what read like the same measurement. An auditd-only host loses one more rule than the stock-build
// fixture suggests, and that rule is not the one you would guess.
func TestStockAndAuditdOnlyAreDifferentHosts(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatalf("load shipped pack: %v", err)
	}
	count := func(avail map[string]bool) (inert, live int) {
		n := len(InertRules([]*Pack{p}, avail))
		return n, len(p.rules()) - n
	}

	if i, l := count(stockAvailable()); i != 6 || l != 14 {
		t.Errorf("stock build: %d inert / %d live, want 6 / 14", i, l)
	}
	if i, l := count(auditdOnlyAvailable()); i != 7 || l != 13 {
		t.Errorf("auditd-only: %d inert / %d live, want 7 / 13 (FINDING-2026-10-26f)", i, l)
	}

	// The single rule that separates them. If a future adapter makes scope.change available on every
	// host, this fails and both findings need re-measuring.
	stock := map[string]bool{}
	for _, r := range InertRules([]*Pack{p}, stockAvailable()) {
		stock[r.ID] = true
	}
	auditd := map[string]bool{}
	for _, r := range InertRules([]*Pack{p}, auditdOnlyAvailable()) {
		auditd[r.ID] = true
	}
	var delta []string
	for id := range auditd {
		if !stock[id] {
			delta = append(delta, id)
		}
	}
	sort.Strings(delta)
	if want := []string{"carbonato.tool.scope_new"}; !reflect.DeepEqual(delta, want) {
		t.Errorf("the stock/auditd-only difference = %v, want %v", delta, want)
	}
}
