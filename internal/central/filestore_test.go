package central

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
)

func TestFileStorePersistsAcrossReopen(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "baselines.json")
	ctx := context.Background()
	key := AgentKey{HostID: "h-1", Root: "/x"}

	// First "process": write a baseline.
	fs, err := NewFileStore(path)
	if err != nil {
		t.Fatalf("NewFileStore: %v", err)
	}
	if err := fs.PutBaseline(ctx, key, inv(fs2("SOUL.md", "aaa", 20, "# SOUL\n"))); err != nil {
		t.Fatalf("PutBaseline: %v", err)
	}

	// Simulate a restart: open a brand-new store on the same file.
	fs2store, err := NewFileStore(path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	got, ok, err := fs2store.GetBaseline(ctx, key)
	if err != nil {
		t.Fatalf("GetBaseline: %v", err)
	}
	if !ok {
		t.Fatal("baseline did not survive the reopen")
	}
	if len(got.Files) != 1 || got.Files[0].SHA256 != "aaa" {
		t.Errorf("baseline content wrong after reopen: %+v", got.Files)
	}
	if got.Files[0].Content != "# SOUL\n" {
		t.Errorf("content did not survive: %q", got.Files[0].Content)
	}
}

func TestFileStoreDriftAfterRestart(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "baselines.json")
	ctx := context.Background()

	// Process 1: enroll via a Service.
	svc1 := NewService(mustFileStore(t, path))
	if _, err := svc1.Ingest(ctx, host, agent, inv(fs2("SOUL.md", "aaa", 10, "old\n"))); err != nil {
		t.Fatal(err)
	}

	// Process 2 (restart): drift must still be detected against the persisted baseline.
	svc2 := NewService(mustFileStore(t, path))
	res, err := svc2.Ingest(ctx, host, agent, inv(fs2("SOUL.md", "bbb", 30, "new payload\n")))
	if err != nil {
		t.Fatal(err)
	}
	if res.FirstSeen {
		t.Error("restarted service wrongly treated agent as first-seen")
	}
	if len(res.Events) != 1 {
		t.Fatalf("want 1 drift event after restart, got %d", len(res.Events))
	}
	if res.Events[0].File.Diff == "" {
		t.Error("expected a diff after restart")
	}
}

func TestFileStoreEmptyFileIsUsable(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "empty.json")
	// Create the file empty (a plausible half-written state).
	if err := writeFile(path, ""); err != nil {
		t.Fatal(err)
	}
	fs, err := NewFileStore(path)
	if err != nil {
		t.Fatalf("NewFileStore on empty file: %v", err)
	}
	_, ok, err := fs.GetBaseline(context.Background(), AgentKey{HostID: "h", Root: "/x"})
	if err != nil || ok {
		t.Errorf("empty store: ok=%v err=%v", ok, err)
	}
}

// helpers

func fs2(path, sum string, size int64, content string) collector.FileState {
	return collector.FileState{Path: path, SHA256: sum, Size: size, Content: content}
}

func mustFileStore(t *testing.T, path string) *FileStore {
	t.Helper()
	st, err := NewFileStore(path)
	if err != nil {
		t.Fatalf("NewFileStore(%s): %v", path, err)
	}
	return st
}

func writeFile(path, content string) error {
	return os.WriteFile(path, []byte(content), 0o644)
}
