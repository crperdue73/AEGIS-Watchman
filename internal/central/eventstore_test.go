package central

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/CRPerdue/agentids/internal/schema"
)

func rec(host, sev, rule string, when time.Time) Record {
	return Record{HostID: host, Severity: sev, RuleID: rule, When: when}
}

func TestMemoryRecordStoreAppendAndRecent(t *testing.T) {
	ctx := context.Background()
	st := NewMemoryRecordStore(RecordRetention{Window: time.Hour, MaxRecords: 100})
	base := time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)
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

func TestBuildFleetViewRollup(t *testing.T) {
	base := time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)
	// newest-first, as Recent returns.
	recs := []Record{
		{HostID: "h1", Severity: "critical", Technique: "phase3/exfiltration", When: base.Add(3 * time.Minute)},
		{HostID: "h2", Severity: "low", When: base.Add(2 * time.Minute)},
		{HostID: "h1", Severity: "high", Technique: "phase3/secret-access", When: base.Add(time.Minute)},
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
	if !v.Hosts[0].LastSeen.Equal(base.Add(3 * time.Minute)) {
		t.Errorf("last_seen = %v, want the latest", v.Hosts[0].LastSeen)
	}
}
