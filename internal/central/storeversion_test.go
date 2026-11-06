package central

// BEAT 756 -- the migration hook the store advertises is never consulted.
//
// `fileStoreDoc` carries a Version field with the comment "@kept versioned so a
// future migration can detect the format." Finding 2026-11-02j said adding the
// agent id to the baseline key is a MIGRATION. So I went to look at the hook
// that migration would use.
//
// It does not exist. `Version` is written on every persist and READ NOWHERE:
// NewFileStore unmarshals the doc and takes doc.Items without ever comparing
// doc.Version to fileStoreVersion. A version field nobody reads is not a
// migration hook; it is a comment with a JSON tag.
//
// Q1  Version is written on persist -- so the field is live on disk
// Q2  THE DEFECT: a file claiming an UNKNOWN future version loads silently, and
//     its items become baselines central will diff against
// Q3  THE DEFECT, harder: a file claiming version 0 / garbage ALSO loads silently
//     -- the field is not merely unpoliced, it is unread
// Q4  control: a genuinely new/empty store still works -- so Q2/Q3 are about the
//     version, not about the store being brittle
// Q5  control: malformed JSON IS rejected -- the store is not simply unvalidated;
//     it validates syntax and skips format
//
// Nothing fixed. The on-disk format is a shipped contract => Dad's call.

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
)

// seedStoreFile writes a store file with an explicit version and one baseline.
func seedStoreFile(t *testing.T, version any, keyPath string) string {
	t.Helper()
	dir := t.TempDir()
	p := filepath.Join(dir, "baselines.json")

	inv := &collector.Inventory{
		Root:  "/srv/x",
		Files: []collector.FileState{{Path: keyPath, SHA256: "aa"}},
	}
	doc := map[string]any{
		"version": version,
		"items":   map[string]*collector.Inventory{"host-1:/srv/x": inv},
	}
	raw, err := json.Marshal(doc)
	if err != nil {
		t.Fatalf("marshal seed: %v", err)
	}
	if err := os.WriteFile(p, raw, 0o600); err != nil {
		t.Fatalf("write seed: %v", err)
	}
	return p
}

// Q1 -- the field is really written, so Q2/Q3 are about a live field, not a
// vestigial struct member.
func TestFileStoreWritesAVersion(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "b.json")
	fs, err := NewFileStore(p)
	if err != nil {
		t.Fatalf("Q1: open: %v", err)
	}
	if err := fs.PutBaseline(context.Background(), AgentKey{HostID: "h", Root: "/r"},
		&collector.Inventory{Root: "/r"}); err != nil {
		t.Fatalf("Q1: put: %v", err)
	}
	raw, err := os.ReadFile(p)
	if err != nil {
		t.Fatalf("Q1: read back: %v", err)
	}
	var doc struct {
		Version *int `json:"version"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("Q1: parse: %v", err)
	}
	if doc.Version == nil {
		t.Fatalf("Q1: persisted store has no version field")
	}
	if *doc.Version != fileStoreVersion {
		t.Fatalf("Q1: version = %d, want %d", *doc.Version, fileStoreVersion)
	}
}

// Q2 -- THE DEFECT. A store from a future version loads without a word.
func TestFutureVersionStoreLoadsSilently(t *testing.T) {
	p := seedStoreFile(t, fileStoreVersion+41, "/srv/x/new-format.md")
	fs, err := NewFileStore(p)
	if err != nil {
		t.Fatalf("Q2: a store claiming version %d should be REJECTED as an unknown "+
			"format. If it now errors, the hook was implemented -- retire Q2/Q3 and say "+
			"the migration path is real: %v", fileStoreVersion+41, err)
	}
	list, err := fs.ListBaselines(context.Background())
	if err != nil {
		t.Fatalf("Q2: list: %v", err)
	}
	if len(list) != 1 {
		t.Fatalf("Q2: expected the unknown-version baseline to be loaded, got %d", len(list))
	}
	// The loaded entry is indistinguishable from a current-format entry.
	if list[0].Key.HostID != "host-1" || list[0].Key.Root != "/srv/x" {
		t.Fatalf("Q2: key parsed as %+v -- the unknown-version item was loaded anyway", list[0].Key)
	}
}

// Q3 -- harder: the version can be absent or nonsense and it still loads. This
// is the difference between "unpoliced" and "unread".
func TestVersionFieldIsUnread(t *testing.T) {
	for _, v := range []any{0, -1, "not-a-number", nil} {
		p := seedStoreFile(t, v, "/srv/x/f.md")
		fs, err := NewFileStore(p)
		if err != nil {
			// A JSON type error on "not-a-number" would be the decoder, not the
			// version check -- record which happened instead of assuming.
			t.Logf("Q3: version=%v rejected: %v", v, err)
			continue
		}
		list, err := fs.ListBaselines(context.Background())
		if err != nil {
			t.Fatalf("Q3: version=%v list: %v", v, err)
		}
		if len(list) == 0 {
			t.Fatalf("Q3: version=%v produced an EMPTY store -- that would be a version "+
				"check by accident; re-measure before editing this pin", v)
		}
	}
}

// Q4 -- control: a plain new store and a plain current-version store both work.
func TestCurrentFormatStillLoads(t *testing.T) {
	p := seedStoreFile(t, fileStoreVersion, "/srv/x/ok.md")
	fs, err := NewFileStore(p)
	if err != nil {
		t.Fatalf("Q4: current version should load: %v", err)
	}
	list, _ := fs.ListBaselines(context.Background())
	if len(list) != 1 {
		t.Fatalf("Q4: current-format store loaded %d baselines, want 1", len(list))
	}

	fresh, err := NewFileStore(filepath.Join(t.TempDir(), "absent.json"))
	if err != nil {
		t.Fatalf("Q4: a missing store file must be a fresh store: %v", err)
	}
	if l, _ := fresh.ListBaselines(context.Background()); len(l) != 0 {
		t.Fatalf("Q4: fresh store is not empty")
	}
}

// Q5 -- control: syntax IS validated. The store is not simply unvalidated; it
// validates JSON and does not validate format. Keep that distinction honest.
func TestMalformedJSONIsRejected(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "bad.json")
	if err := os.WriteFile(p, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := NewFileStore(p); err == nil {
		t.Fatalf("Q5: malformed JSON loaded without error -- the store has no validation " +
			"at all, which is a bigger claim than this finding makes")
	}
}
