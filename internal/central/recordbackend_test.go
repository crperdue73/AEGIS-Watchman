package central

import (
	"path/filepath"
	"testing"
)

// TestRecordStoreFnDefaultsToFile: a records path with no record_backend yields
// the zero-dependency JSON file store — the default this repo promises.
func TestRecordStoreFnDefaultsToFile(t *testing.T) {
	c := Config{Records: filepath.Join(t.TempDir(), "records.json")}
	rs, err := c.RecordStoreFn()
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := rs.(*FileRecordStore); !ok {
		t.Fatalf("default backend = %T, want *FileRecordStore", rs)
	}
}

// TestRecordStoreFnNoPathIsMemory: no records path -> in-memory (view works,
// resets on restart).
func TestRecordStoreFnNoPathIsMemory(t *testing.T) {
	c := Config{}
	rs, err := c.RecordStoreFn()
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := rs.(*MemoryRecordStore); !ok {
		t.Fatalf("no-path backend = %T, want *MemoryRecordStore", rs)
	}
}

// TestRecordBackendOrDefault names the default explicitly for the startup log.
func TestRecordBackendOrDefault(t *testing.T) {
	if got := (Config{}).RecordBackendOrDefault(); got != "file" {
		t.Errorf("default backend name = %q, want file", got)
	}
	if got := (Config{RecordBackend: "sqlite"}).RecordBackendOrDefault(); got != "sqlite" {
		t.Errorf("explicit backend name = %q, want sqlite", got)
	}
}

// TestResolvedRecordRetentionFillsDefaults: the startup log must report what the
// store will actually enforce, not the raw config. An operator who sets a
// records path and leaves the retention fields unset is relying on the
// documented 7d/10000 defaults — the log must say so, not "0d/0" (which reads
// as unbounded, the opposite of the truth).
func TestResolvedRecordRetentionFillsDefaults(t *testing.T) {
	got := (Config{}).ResolvedRecordRetention()
	if got.Window != DefaultRecordRetention.Window {
		t.Errorf("default window = %v, want %v", got.Window, DefaultRecordRetention.Window)
	}
	if got.MaxRecords != DefaultRecordRetention.MaxRecords {
		t.Errorf("default max = %d, want %d", got.MaxRecords, DefaultRecordRetention.MaxRecords)
	}
}

// TestResolvedRecordRetentionHonorsExplicit: explicit values are passed through
// untouched, and the resolution the log uses is the same one the store uses.
func TestResolvedRecordRetentionHonorsExplicit(t *testing.T) {
	c := Config{RecordRetentionDays: 30, RecordMax: 500}
	got := c.ResolvedRecordRetention()
	if got.Window.Hours() != 30*24 {
		t.Errorf("window = %v, want 30d", got.Window)
	}
	if got.MaxRecords != 500 {
		t.Errorf("max = %d, want 500", got.MaxRecords)
	}
}

// TestRecordStoreFnSQLiteRequiresTag: asking for sqlite in a build without the
// tag is a clear error, not a silent fallback to the file backend — a silent
// fallback would ship a config the operator did not get. In the -tags sqlite
// build this asserts the opposite (it succeeds), so the same test pins both.
func TestRecordStoreFnSQLiteRequiresTag(t *testing.T) {
	c := Config{Records: filepath.Join(t.TempDir(), "records.db"), RecordBackend: "sqlite"}
	rs, err := c.RecordStoreFn()
	if sqliteRecordStoreFn == nil {
		if err == nil {
			t.Fatal("expected an error for sqlite without -tags sqlite")
		}
		return
	}
	if err != nil {
		t.Fatalf("sqlite backend with tag: %v", err)
	}
	if !testing.Short() {
		defer func() {
			if c, ok := rs.(interface{ Close() error }); ok {
				_ = c.Close()
			}
		}()
	}
}
