package central

// BEAT 757 -- I predicted the baseline store leaks its slice. It does not.
//
// Continuing the identity/migration thread (v -> j -> k), I went at the layer
// under the key: what does GetBaseline actually RETURN? `cloneInventory` does a
// shallow copy of the slice -- `make` + `copy` -- which is a deep copy for a
// flat struct and a live alias the moment FileState gains a reference type.
//
// I EXPECTED to find a live alias and MEASURED that there is none: `make` gives
// cloneInventory a fresh backing array and `copy` fills it. My prediction was
// wrong; the code is right. S2 is kept pointing the direction the measurement
// went, not the direction I guessed.
//
// S1  writing a field on a GetBaseline result does NOT reach the store
// S2  the two inventories share NO backing array (pointer identity) -- and the
//     clone does not inherit the source's spare capacity, so append is safe too
// S3  the ingest path does not write into the caller's inventory (measured)
// S4  control: append in the caller does not affect the store
// S5  the guard that should exist: a forward-compat note, asserted

import (
	"context"
	"reflect"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// S1 -- control: the happy path. Mutating the returned inventory must not reach
// the stored baseline.
func TestGetBaselineReturnsAnIndependentValue(t *testing.T) {
	ctx := context.Background()
	fs, err := NewFileStore(t.TempDir() + "/b.json")
	if err != nil {
		t.Fatalf("S1: %v", err)
	}
	key := AgentKey{HostID: "h", Root: "/srv/x"}
	if err := fs.PutBaseline(ctx, key, &collector.Inventory{
		Root: "/srv/x",
		Files: []collector.FileState{
			{Path: "/srv/x/a.md", SHA256: "aa", Size: 1},
		},
	}); err != nil {
		t.Fatalf("S1: put: %v", err)
	}

	got, ok, err := fs.GetBaseline(ctx, key)
	if err != nil || !ok {
		t.Fatalf("S1: get: ok=%v err=%v", ok, err)
	}
	got.Files[0].SHA256 = "TAMPERED"
	got.Files[0].Size = 999

	again, _, err := fs.GetBaseline(ctx, key)
	if err != nil {
		t.Fatalf("S1: reget: %v", err)
	}
	if again.Files[0].SHA256 != "aa" || again.Files[0].Size != 1 {
		t.Fatalf("S1: mutating a GetBaseline result reached the store (%q/%d) -- the "+
			"store now leaks mutable state", again.Files[0].SHA256, again.Files[0].Size)
	}
}

// S2 -- I PREDICTED AN ALIAS AND THERE ISN'T ONE. `make` allocates a NEW backing
// array; `copy` fills it. The two inventories share nothing. This test was
// written expecting shared storage and failed on first run; the finding was
// wrong, not the code. Kept as a pin in the direction the measurement actually
// went, so the next reader inherits the answer instead of my guess.
func TestCloneDoesNotShareTheBackingArray(t *testing.T) {
	src := &collector.Inventory{
		Root:  "/srv/x",
		Files: []collector.FileState{{Path: "/srv/x/a.md", SHA256: "aa"}},
	}
	cp := cloneInventory(src)
	if len(cp.Files) != 1 {
		t.Fatalf("S2: clone lost files")
	}
	if reflect.ValueOf(cp.Files).Index(0).Addr().Pointer() ==
		reflect.ValueOf(src.Files).Index(0).Addr().Pointer() {
		t.Fatalf("S2: clone SHARES the backing array -- cloneInventory changed shape " +
			"and the shallow-clone question is live again; re-measure S1/S5")
	}
	// And a write through one is genuinely invisible in the other.
	src.Files[0].SHA256 = "written-through-src"
	if cp.Files[0].SHA256 != "aa" {
		t.Fatalf("S2: write through the source reached the clone (%q)", cp.Files[0].SHA256)
	}
	// Bounded by design: copy() does not extend cap, so a caller's append
	// allocates rather than writing into the source's slack.
	if cap(cp.Files) == cap(src.Files) && cap(src.Files) > len(src.Files) {
		t.Fatalf("S2: clone inherited the source's spare capacity -- append would " +
			"then write into shared slack")
	}
}

// S3 -- is the alias observable through the real ingest path? Measured by
// exercising Ingest->GetBaseline and checking Ingest does not write into
// inv.Files afterwards. If Ingest ever sorts or rewrites inv in place, this
// alias becomes a live cross-agent data bug, so it is asserted structurally.
func TestIngestDoesNotWriteIntoTheCallersInventory(t *testing.T) {
	ctx := context.Background()
	svc := NewService(NewMemoryStore())
	inv := &collector.Inventory{
		Root: "/srv/x",
		Files: []collector.FileState{
			{Path: "/srv/x/b.md", SHA256: "bb"},
			{Path: "/srv/x/a.md", SHA256: "aa"},
		},
	}
	// Deliberately unsorted, so an in-place sort would be visible.
	before := append([]collector.FileState(nil), inv.Files...)

	if _, err := svc.Ingest(ctx, schema.Host{ID: "host-1"}, schema.Agent{ID: "helper", Root: "/srv/x"}, inv); err != nil {
		t.Fatalf("S3: ingest: %v", err)
	}
	for i := range before {
		if inv.Files[i].Path != before[i].Path || inv.Files[i].SHA256 != before[i].SHA256 {
			t.Fatalf("S3: Ingest wrote into the caller's inventory (index %d: %q -> %q). "+
				"Inventory is shared with the store's backing array, so that write is a "+
				"cross-agent data bug, not a style issue", i, before[i].Path, inv.Files[i].Path)
		}
	}
}

// S4 -- control: append in the caller allocates and does not touch the store.
func TestAppendInCallerDoesNotReachTheStore(t *testing.T) {
	ctx := context.Background()
	fs, err := NewFileStore(t.TempDir() + "/b.json")
	if err != nil {
		t.Fatalf("S4: %v", err)
	}
	key := AgentKey{HostID: "h", Root: "/srv/x"}
	_ = fs.PutBaseline(ctx, key, &collector.Inventory{
		Root:  "/srv/x",
		Files: []collector.FileState{{Path: "/srv/x/a.md", SHA256: "aa"}},
	})
	got, _, _ := fs.GetBaseline(ctx, key)
	got.Files = append(got.Files, collector.FileState{Path: "/srv/x/injected.md", SHA256: "evil"})

	again, _, _ := fs.GetBaseline(ctx, key)
	if len(again.Files) != 1 {
		t.Fatalf("S4: append in the caller changed the store (%d files) -- copy did "+
			"not isolate the length", len(again.Files))
	}
}

// S5 -- the forward-compat assertion. cloneInventory is shallow BY CONSTRUCTION
// and correct only while FileState stays flat. This test fails the day that
// stops being true, which is the day S1's guarantee silently stops holding.
func TestFileStateIsFlatSoShallowCloneIsSound(t *testing.T) {
	typ := reflect.TypeOf(collector.FileState{})
	for i := 0; i < typ.NumField(); i++ {
		f := typ.Field(i)
		switch f.Type.Kind() {
		case reflect.Ptr, reflect.Map, reflect.Slice, reflect.Interface, reflect.Func, reflect.Chan:
			t.Fatalf("S5: FileState.%s is a %s -- cloneInventory copies the slice "+
				"shallowly, so this field is now SHARED between the store and every "+
				"caller. Make cloneInventory deep for this field before trusting S1",
				f.Name, f.Type.Kind())
		}
	}

	invTyp := reflect.TypeOf(collector.Inventory{})
	for i := 0; i < invTyp.NumField(); i++ {
		f := invTyp.Field(i)
		if f.Name == "Files" {
			continue // the slice cloneInventory copies explicitly
		}
		switch f.Type.Kind() {
		case reflect.Ptr, reflect.Map, reflect.Slice, reflect.Interface, reflect.Func, reflect.Chan:
			t.Fatalf("S5: Inventory.%s is a %s and cloneInventory does not copy it -- "+
				"the store now shares it with callers", f.Name, f.Type.Kind())
		}
	}
}
