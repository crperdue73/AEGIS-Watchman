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

	// The classes a stock collector emits: auditd (when audit_log is set) plus
	// the default-on scope.change shim. eBPF (file.access) and docker
	// (container.start) are both opt-in and off by default.
	stock := map[string]bool{
		"process.exec": true, "net.egress": true, "net.listen": true,
		"file.write": true, "scope.change": true,
	}
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
