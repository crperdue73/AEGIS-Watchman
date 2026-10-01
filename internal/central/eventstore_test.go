package central

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

func rec(host, sev, rule string, when time.Time) Record {
	return Record{HostID: host, Severity: sev, RuleID: rule, When: when}
}

func TestMemoryRecordStoreAppendAndRecent(t *testing.T) {
	ctx := context.Background()
	st := NewMemoryRecordStore(RecordRetention{Window: time.Hour, MaxRecords: 100})
	base := time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)
	// Pin the store's clock. Retention prunes against now, so a test that leaves
	// nowFn as the real clock is a time bomb: it passes only while the hardcoded
	// base is still inside the window, then fails once wall time moves past it.
	st.nowFn = func() time.Time { return base.Add(3 * time.Minute) }
	_ = st.Append(ctx, rec("h1", "high", "r.1", base))
	_ = st.Append(ctx, rec("h2", "low", "r.2", base.Add(time.Minute)))
	_ = st.Append(ctx, rec("h1", "critical", "r.3", base.Add(2*time.Minute)))

	got, err := st.Recent(ctx, "", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 records, got %d", len(got))
	}
	// Newest first.
	if got[0].RuleID != "r.3" {
		t.Errorf("newest-first order broken: got %s first", got[0].RuleID)
	}
	// Host filter.
	only, _ := st.Recent(ctx, "h1", 10)
	if len(only) != 2 || only[0].HostID != "h1" {
		t.Fatalf("host filter failed: %+v", only)
	}
	// Limit.
	lim, _ := st.Recent(ctx, "", 1)
	if len(lim) != 1 || lim[0].RuleID != "r.3" {
		t.Fatalf("limit failed: %+v", lim)
	}
}

func TestMemoryRecordStoreRetentionWindow(t *testing.T) {
	ctx := context.Background()
	st := NewMemoryRecordStore(RecordRetention{Window: time.Hour, MaxRecords: 100})
	now := time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)
	st.nowFn = func() time.Time { return now }
	// One record inside the window, one well outside.
	_ = st.Append(ctx, rec("h1", "high", "old", now.Add(-3*time.Hour)))
	_ = st.Append(ctx, rec("h1", "high", "new", now))

	got, _ := st.Recent(ctx, "", 10)
	if len(got) != 1 || got[0].RuleID != "new" {
		t.Fatalf("retention window did not drop the stale record: %+v", got)
	}
}

func TestMemoryRecordStoreCap(t *testing.T) {
	ctx := context.Background()
	st := NewMemoryRecordStore(RecordRetention{Window: time.Hour, MaxRecords: 2})
	now := time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)
	st.nowFn = func() time.Time { return now }
	for i := 0; i < 5; i++ {
		_ = st.Append(ctx, rec("h1", "high", "r", now.Add(time.Duration(i)*time.Second)))
	}
	got, _ := st.Recent(ctx, "", 100)
	if len(got) != 2 {
		t.Fatalf("cap not enforced: kept %d, want 2", len(got))
	}
}

func TestFileRecordStoreSurvivesReload(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "records.json")
	now := time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)

	f, err := NewFileRecordStore(path, RecordRetention{Window: 24 * time.Hour, MaxRecords: 100})
	if err != nil {
		t.Fatal(err)
	}
	f.nowFn = func() time.Time { return now }
	if err := f.Append(ctx, rec("h1", "critical", "carbonato.exfil", now)); err != nil {
		t.Fatal(err)
	}

	// Reopen: the record must be there — the whole point of retaining.
	re, err := NewFileRecordStore(path, RecordRetention{Window: 24 * time.Hour, MaxRecords: 100})
	if err != nil {
		t.Fatal(err)
	}
	got, _ := re.Recent(ctx, "", 10)
	if len(got) != 1 || got[0].RuleID != "carbonato.exfil" {
		t.Fatalf("durable record did not survive reload: %+v", got)
	}
}

// TestIngestRetainsRecords proves the wire-through: an ingested action becomes
// queryable in the fleet view.
func TestIngestRetainsRecords(t *testing.T) {
	ctx := context.Background()
	svc := NewService(NewMemoryStore())
	rs := NewMemoryRecordStore(DefaultRecordRetention)
	svc.SetRecordStore(rs)

	host := schema.Host{ID: "builder-01", Name: "builder"}
	agent := schema.Agent{ID: "a1", Root: "/home/agent"}
	events := []schema.Event{
		{
			Schema: schema.Version, Event: schema.EventBehaviorRuleMatch,
			Severity: schema.SeverityCritical, Host: host,
			Rule:   &schema.Rule{ID: "carbonato.exfil.egress_after_change", Pack: "carbonato"},
			Action: &schema.Action{Type: "net.egress"},
		},
	}
	n, err := svc.IngestActions(ctx, host, agent, events)
	if err != nil || n != 1 {
		t.Fatalf("IngestActions = %d, %v", n, err)
	}

	got, _ := rs.Recent(ctx, "builder-01", 10)
	if len(got) != 1 {
		t.Fatalf("want 1 retained record, got %d", len(got))
	}
	if got[0].RuleID != "carbonato.exfil.egress_after_change" {
		t.Errorf("rule id = %q", got[0].RuleID)
	}
	if got[0].Severity != schema.SeverityCritical {
		t.Errorf("severity = %q", got[0].Severity)
	}
	if got[0].ActionType != "net.egress" {
		t.Errorf("action type = %q", got[0].ActionType)
	}
}

// TestIngestRetainsEvenWithoutAlerter: retention is independent of alerting, so
// a fleet view works with alerting off (the dry-run / quiet deployment case).
func TestIngestRetainsEvenWithoutAlerter(t *testing.T) {
	ctx := context.Background()
	svc := NewService(NewMemoryStore())
	rs := NewMemoryRecordStore(DefaultRecordRetention)
	svc.SetRecordStore(rs)
	// No alerter installed.
	host := schema.Host{ID: "h"}
	events := []schema.Event{{Event: schema.EventIdentityDrift, Severity: schema.SeverityHigh, Host: host, File: &schema.FileInfo{Path: "/x"}}}
	if _, err := svc.IngestActions(ctx, host, schema.Agent{Root: "/r"}, events); err != nil {
		t.Fatal(err)
	}
	got, _ := rs.Recent(ctx, "", 10)
	if len(got) != 1 {
		t.Fatalf("record not retained without alerter: %d", len(got))
	}
}

// TestIngestRetainsRuleTechnique: a rule match carries its technique on the
// event's Rule, and it must survive into the retained record and the fleet
// rollup's by_technique — otherwise the advertised technique breakdown is empty.
func TestIngestRetainsRuleTechnique(t *testing.T) {
	ctx := context.Background()
	svc := NewService(NewMemoryStore())
	rs := NewMemoryRecordStore(DefaultRecordRetention)
	svc.SetRecordStore(rs)

	host := schema.Host{ID: "h1", Name: "one"}
	agent := schema.Agent{Root: "/r"}
	events := []schema.Event{{
		Schema: schema.Version, Event: schema.EventBehaviorRuleMatch,
		Severity: schema.SeverityHigh, Host: host,
		Rule:   &schema.Rule{ID: "carbonato.exfil.egress_after_change", Pack: "carbonato", Technique: "phase3/exfiltration"},
		Action: &schema.Action{Type: "net.egress"},
	}}
	if _, err := svc.IngestActions(ctx, host, agent, events); err != nil {
		t.Fatal(err)
	}

	recs, err := rs.Recent(ctx, "h1", 10)
	if err != nil || len(recs) != 1 {
		t.Fatalf("records = %d, %v", len(recs), err)
	}
	if recs[0].Technique != "phase3/exfiltration" {
		t.Errorf("record technique = %q, want phase3/exfiltration", recs[0].Technique)
	}
	view := buildFleetView(recs, 10)
	if len(view.Hosts) != 1 || view.Hosts[0].ByTechnique["phase3/exfiltration"] != 1 {
		t.Errorf("fleet by_technique not populated: %+v", view.Hosts)
	}
}

// TestIngestRetainsActionSource covers the honesty marker end to end: a
// scope.change match whose action carries detail.source=heuristic must reach
// the retained record (and the fleet rollup) as action_source, so an operator
// can tell a derived signal from an authoritative one.
func TestIngestRetainsActionSource(t *testing.T) {
	ctx := context.Background()
	svc := NewService(NewMemoryStore())
	rs := NewMemoryRecordStore(DefaultRecordRetention)
	svc.SetRecordStore(rs)

	host := schema.Host{ID: "h1"}
	agent := schema.Agent{Root: "/r"}
	events := []schema.Event{{
		Schema: schema.Version, Event: schema.EventBehaviorRuleMatch,
		Severity: schema.SeverityHigh, Host: host,
		Rule: &schema.Rule{ID: "carbonato.tool.scope_new", Technique: "phase3/scope-expansion"},
		Action: &schema.Action{Type: "scope.change", Detail: map[string]any{
			"source": "heuristic", "kind": "endpoint", "novel": "true",
		}},
	}}
	if _, err := svc.IngestActions(ctx, host, agent, events); err != nil {
		t.Fatal(err)
	}

	recs, err := rs.Recent(ctx, "h1", 10)
	if err != nil || len(recs) != 1 {
		t.Fatalf("records = %d, %v", len(recs), err)
	}
	if recs[0].ActionSource != "heuristic" {
		t.Errorf("record action_source = %q, want heuristic", recs[0].ActionSource)
	}
	view := buildFleetView(recs, 10)
	if len(view.Hosts) != 1 || view.Hosts[0].BySource["heuristic"] != 1 {
		t.Errorf("fleet by_source not populated: %+v", view.Hosts)
	}
}

// An ordinary event with no provenance marker records an empty action_source —
// the field means "unmarked", never an accidental claim of trust.
func TestRecordFromEventNoSourceIsEmpty(t *testing.T) {
	ev := schema.Event{
		Schema: schema.Version, Event: schema.EventBehaviorRuleMatch,
		Host:   schema.Host{ID: "h1"},
		Action: &schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "/bin/sh"}},
	}
	rec := RecordFromEvent(ev.Host, schema.Agent{}, ev, time.Now().UTC())
	if rec.ActionSource != "" {
		t.Errorf("unmarked action got action_source=%q, want empty", rec.ActionSource)
	}
}

// TestRecordFromEventCarriesPack proves the schema->record mapping copies the
// rule's pack and version, not just its id: the fleet view's By pack rollup is
// built from these two fields, so a mapping that dropped them would leave the
// column silently empty.
func TestRecordFromEventCarriesPack(t *testing.T) {
	host := schema.Host{ID: "h1"}
	ev := schema.Event{
		Schema: schema.Version, Event: schema.EventBehaviorRuleMatch,
		Severity: schema.SeverityHigh, Host: host,
		Rule: &schema.Rule{
			ID: "carbonato.persist.file_write", Pack: "carbonato", Version: "7",
			Technique: "phase3/persistence",
		},
	}
	rec := RecordFromEvent(host, schema.Agent{}, ev, time.Now().UTC())
	if rec.RulePack != "carbonato" || rec.RuleVersion != "7" {
		t.Errorf("record pack = %q@%q, want carbonato@7", rec.RulePack, rec.RuleVersion)
	}
	if got := packLabel(rec.RulePack, rec.RuleVersion); got != "carbonato@7" {
		t.Errorf("packLabel = %q, want carbonato@7", got)
	}
}

// TestActionSourceRoundTripsEveryBackend keeps the provenance marker a
// first-class record field, not something only the in-memory store happens to
// keep: every backend in the contract matrix must return it.
func TestActionSourceRoundTripsEveryBackend(t *testing.T) {
	ctx := context.Background()
	base := time.Now().UTC().Truncate(time.Second)
	for name, mk := range recordStoreImpls(t) {
		t.Run(name, func(t *testing.T) {
			st := mk()
			rec := rec("h1", "high", "carbonato.tool.scope_new", base)
			rec.ActionSource = "heuristic"
			if err := st.Append(ctx, rec); err != nil {
				t.Fatal(err)
			}
			got, err := st.Query(ctx, RecordQuery{HostID: "h1"})
			if err != nil || len(got) != 1 {
				t.Fatalf("query = %d, %v", len(got), err)
			}
			if got[0].ActionSource != "heuristic" {
				t.Errorf("%s: action_source did not round-trip: %q", name, got[0].ActionSource)
			}
		})
	}
}

// TestRulePackRoundTripsEveryBackend keeps the pack provenance a first-class
// record field across every backend, like action_source: the SQLite backend
// stores it in real columns, so a query must return it.
func TestRulePackRoundTripsEveryBackend(t *testing.T) {
	ctx := context.Background()
	base := time.Now().UTC().Truncate(time.Second)
	for name, mk := range recordStoreImpls(t) {
		t.Run(name, func(t *testing.T) {
			st := mk()
			r := rec("h1", "high", "carbonato.persist.file_write", base)
			r.RulePack = "carbonato"
			r.RuleVersion = "7"
			if err := st.Append(ctx, r); err != nil {
				t.Fatal(err)
			}
			got, err := st.Query(ctx, RecordQuery{HostID: "h1"})
			if err != nil || len(got) != 1 {
				t.Fatalf("query = %d, %v", len(got), err)
			}
			if got[0].RulePack != "carbonato" || got[0].RuleVersion != "7" {
				t.Errorf("%s: pack did not round-trip: %q@%q", name, got[0].RulePack, got[0].RuleVersion)
			}
		})
	}
}

func TestBuildFleetViewRollup(t *testing.T) {
	base := time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)
	// newest-first, as Recent returns.
	recs := []Record{
		{HostID: "h1", Severity: "critical", Technique: "phase3/exfiltration", RulePack: "carbonato", RuleVersion: "7", When: base.Add(3 * time.Minute)},
		{HostID: "h2", Severity: "low", When: base.Add(2 * time.Minute)},
		{HostID: "h1", Severity: "high", Technique: "phase3/secret-access", RulePack: "carbonato", RuleVersion: "7", When: base.Add(time.Minute)},
	}
	v := buildFleetView(recs, 10)
	if v.TotalEvents != 3 {
		t.Fatalf("total = %d", v.TotalEvents)
	}
	if len(v.Hosts) != 2 {
		t.Fatalf("hosts = %d, want 2", len(v.Hosts))
	}
	// h1 (critical) must lead.
	if v.Hosts[0].HostID != "h1" {
		t.Fatalf("worst-first ordering broken: %s leads", v.Hosts[0].HostID)
	}
	if v.Hosts[0].MaxSeverity != "critical" || v.Hosts[0].Total != 2 {
		t.Errorf("h1 rollup = %+v", v.Hosts[0])
	}
	if v.Hosts[0].BySeverity["high"] != 1 || v.Hosts[0].BySeverity["critical"] != 1 {
		t.Errorf("h1 by_severity = %+v", v.Hosts[0].BySeverity)
	}
	if v.Hosts[0].ByTechnique["phase3/exfiltration"] != 1 {
		t.Errorf("h1 by_technique = %+v", v.Hosts[0].ByTechnique)
	}
	// Pack provenance rolls up as name@version; a host whose records carry no
	// pack gets no bucket (not an empty-string key).
	if v.Hosts[0].ByPack["carbonato@7"] != 2 {
		t.Errorf("h1 by_pack = %+v, want carbonato@7=2", v.Hosts[0].ByPack)
	}
	if len(v.Hosts[1].ByPack) != 0 {
		t.Errorf("h2 by_pack = %+v, want empty (no pack on its records)", v.Hosts[1].ByPack)
	}
	if !v.Hosts[0].LastSeen.Equal(base.Add(3 * time.Minute)) {
		t.Errorf("last_seen = %v, want the latest", v.Hosts[0].LastSeen)
	}
}

// ---------------------------------------------------------------------------
// Query contract tests. These run against BOTH backends via recordStoreImpls,
// so a future SQL backend is "done" when it passes them unchanged — the point
// of the interface (see docs/P2.1C-RECORD-STORE-BACKENDS.md).
// ---------------------------------------------------------------------------

// sqliteImpls is nil in the default build and set by init() in the -tags
// sqlite build (eventstore_sqlite_test.go). Kept here so the contract-test
// matrix includes every compiled-in backend without a build tag in this file.
var sqliteImpls func(t *testing.T) map[string]func() RecordStore

// recordStoreImpls returns one fresh store of each implementation, so a single
// table-driven test pins identical behavior across backends.
func recordStoreImpls(t *testing.T) map[string]func() RecordStore {
	t.Helper()
	impls := map[string]func() RecordStore{
		"memory": func() RecordStore { return NewMemoryRecordStore(DefaultRecordRetention) },
		"file": func() RecordStore {
			f, err := NewFileRecordStore(filepath.Join(t.TempDir(), "records.json"), DefaultRecordRetention)
			if err != nil {
				t.Fatal(err)
			}
			return f
		},
	}
	if sqliteImpls != nil {
		for name, mk := range sqliteImpls(t) {
			impls[name] = mk
		}
	}
	return impls
}

func TestQueryTimeRange(t *testing.T) {
	ctx := context.Background()
	// Clock-relative base: the stores prune against real now, so a hardcoded date
	// near "today" would make this pass only for a few days after it was written.
	base := time.Now().UTC().Truncate(time.Second)
	for name, mk := range recordStoreImpls(t) {
		t.Run(name, func(t *testing.T) {
			st := mk()
			_ = st.Append(ctx, rec("h1", "high", "a", base.Add(-2*time.Hour)))
			_ = st.Append(ctx, rec("h1", "high", "b", base))
			_ = st.Append(ctx, rec("h1", "high", "c", base.Add(2*time.Hour)))

			// Since inclusive.
			got, _ := st.Query(ctx, RecordQuery{Since: base})
			if len(got) != 2 {
				t.Fatalf("since filter: got %d, want 2", len(got))
			}
			// Until exclusive: the record exactly at `base` must be excluded.
			got, _ = st.Query(ctx, RecordQuery{Until: base})
			if len(got) != 1 || got[0].RuleID != "a" {
				t.Fatalf("until filter: %+v", got)
			}
			// Window.
			got, _ = st.Query(ctx, RecordQuery{Since: base, Until: base.Add(time.Hour)})
			if len(got) != 1 || got[0].RuleID != "b" {
				t.Fatalf("window filter: %+v", got)
			}
		})
	}
}

func TestQueryMinSeverity(t *testing.T) {
	ctx := context.Background()
	base := time.Now().UTC().Truncate(time.Second)
	for name, mk := range recordStoreImpls(t) {
		t.Run(name, func(t *testing.T) {
			st := mk()
			_ = st.Append(ctx, rec("h1", "low", "l", base))
			_ = st.Append(ctx, rec("h1", "high", "h", base))
			_ = st.Append(ctx, rec("h1", "critical", "c", base))

			got, _ := st.Query(ctx, RecordQuery{MinSeverity: "high"})
			if len(got) != 2 {
				t.Fatalf("min severity high: got %d, want 2", len(got))
			}
			for _, r := range got {
				if r.Severity == "low" {
					t.Errorf("low record leaked past a high floor: %+v", r)
				}
			}
			got, _ = st.Query(ctx, RecordQuery{MinSeverity: "critical"})
			if len(got) != 1 || got[0].RuleID != "c" {
				t.Fatalf("min severity critical: %+v", got)
			}
		})
	}
}

func TestQueryCombinedAndLimit(t *testing.T) {
	ctx := context.Background()
	base := time.Now().UTC().Truncate(time.Second)
	for name, mk := range recordStoreImpls(t) {
		t.Run(name, func(t *testing.T) {
			st := mk()
			_ = st.Append(ctx, rec("h1", "critical", "c1", base.Add(1*time.Minute)))
			_ = st.Append(ctx, rec("h2", "critical", "c2", base.Add(2*time.Minute)))
			_ = st.Append(ctx, rec("h1", "low", "l1", base.Add(3*time.Minute)))
			_ = st.Append(ctx, rec("h1", "critical", "c3", base.Add(4*time.Minute)))

			got, _ := st.Query(ctx, RecordQuery{HostID: "h1", MinSeverity: "high", Limit: 1})
			if len(got) != 1 {
				t.Fatalf("combined filter+limit: got %d, want 1", len(got))
			}
			// Newest-first: c3 (4m) precedes c1 (1m).
			if got[0].RuleID != "c3" {
				t.Errorf("expected newest c3, got %s", got[0].RuleID)
			}
		})
	}
}
