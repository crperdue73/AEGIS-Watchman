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
