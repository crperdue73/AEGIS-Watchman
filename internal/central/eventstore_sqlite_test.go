//go:build sqlite

package central

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

// init registers the SQLite backend into the shared recordStoreImpls matrix so
// the SAME contract tests (TestQuery*) run against it. This is the acceptance
// criterion from docs/P2.1C-RECORD-STORE-BACKENDS.md: a new backend is "done"
// when it passes the contract tests unchanged.
func init() {
	sqliteImpls = func(t *testing.T) map[string]func() RecordStore {
		return map[string]func() RecordStore{
			"sqlite": func() RecordStore {
				s, err := NewSQLiteRecordStore(filepath.Join(t.TempDir(), "records.db"), DefaultRecordRetention)
				if err != nil {
					t.Fatalf("open sqlite store: %v", err)
				}
				t.Cleanup(func() { _ = s.Close() })
				return s
			},
		}
	}
}

// TestSQLiteSurvivesReload proves durability across a real reopen — the whole
// point of the backend. (The JSON file has the same test; this pins it for SQL.)
func TestSQLiteSurvivesReload(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "records.db")
	now := time.Now().UTC()

	a, err := NewSQLiteRecordStore(path, RecordRetention{Window: 24 * time.Hour, MaxRecords: 100})
	if err != nil {
		t.Fatal(err)
	}
	if err := a.Append(ctx, Record{HostID: "h1", Severity: "critical", RuleID: "carbonato.exfil", When: now}); err != nil {
		t.Fatal(err)
	}
	if err := a.Close(); err != nil {
		t.Fatal(err)
	}

	b, err := NewSQLiteRecordStore(path, RecordRetention{Window: 24 * time.Hour, MaxRecords: 100})
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	got, err := b.Query(ctx, RecordQuery{HostID: "h1"})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].RuleID != "carbonato.exfil" {
		t.Fatalf("sqlite record did not survive reopen: %+v", got)
	}
}
