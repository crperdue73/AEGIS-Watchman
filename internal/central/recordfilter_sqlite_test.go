//go:build sqlite

package central

import (
	"context"
	"path/filepath"
	"testing"
)

// Beat 802, the tagged half. The SQLite store re-expresses recordMatches as a WHERE clause
// (`severity_rank >= ?`, `when_unix >= ?`, `when_unix < ?`, `host_id = ?`). Those are four
// independent opportunities to disagree with the Go predicate -- an inclusive bound written as
// `>`, a floor written as `>`, a host comparison written with LIKE -- and every one of them would
// be invisible to the untagged suite, because the untagged suite never opens this store.
func TestRecordFiltersAgreeMemoryVsSQLite(t *testing.T) {
	mem := NewMemoryRecordStore(RecordRetention{})
	sq, err := NewSQLiteRecordStore(filepath.Join(t.TempDir(), "recs.db"), RecordRetention{})
	if err != nil {
		t.Fatalf("open sqlite store: %v", err)
	}
	t.Cleanup(func() { _ = sq.Close() })
	ctx := context.Background()
	for _, r := range filterCorpus {
		if err := mem.Append(ctx, r); err != nil {
			t.Fatalf("mem append: %v", err)
		}
		if err := sq.Append(ctx, r); err != nil {
			t.Fatalf("sqlite append: %v", err)
		}
	}
	compareStores(t, "memory", mem, "sqlite", sq)
}
