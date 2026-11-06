package central

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// BEAT 795. Finding 2026-11-02g: a batch central accepts nothing from still
// answers 202, so a collector deletes spooled evidence that was never stored.
// The wire contract is Dad's call; this beat makes the loss COUNTABLE (option 3).
//
// The assertions below deliberately do NOT check the HTTP status. Asserting 202
// would pin the defect in place; the point is that the loss is now visible
// somewhere regardless of what the status says.

func mkEvents(n int, name string) []schema.Event {
	out := make([]schema.Event, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, schema.Event{Event: name})
	}
	return out
}

func TestIngestActionsCountsUnnamedDrops(t *testing.T) {
	svc := NewService(NewMemoryStore())
	ctx := context.Background()
	host := schema.Host{ID: "h1"}

	if got := svc.DroppedUnnamed(); got != 0 {
		t.Fatalf("fresh service dropped=%d, want 0", got)
	}

	// Three unnamed + two named. The named ones are accepted; the unnamed must be
	// counted, not merely skipped.
	events := []schema.Event{{Event: ""}, {Event: ""}, {Event: "file.access"}, {Event: ""}, {Event: "net.connect"}}
	accepted, err := svc.IngestActions(ctx, host, schema.Agent{}, events)
	if err != nil {
		t.Fatalf("IngestActions: %v", err)
	}
	if accepted != 2 {
		t.Fatalf("accepted=%d, want 2", accepted)
	}
	if got := svc.DroppedUnnamed(); got != 3 {
		t.Errorf("dropped=%d after 3 unnamed events, want 3 -- an uncounted drop is a silent one", got)
	}

	// A total drop must ALSO count -- and since finding 2026-11-02g's fix it must
	// ALSO refuse. This block used to assert `err == nil`; that was the defect
	// itself, not a contract. Both halves are pinned: the count rises AND the
	// caller is told (a counter nobody reads is not a fix for evidence loss).
	accepted, err = svc.IngestActions(ctx, host, schema.Agent{}, []schema.Event{{Event: ""}, {Event: ""}})
	if !errors.Is(err, ErrTotalDrop) {
		t.Fatalf("total-drop IngestActions err = %v, want ErrTotalDrop: a batch nothing "+
			"was retained from must not read as success to a caller that deletes on success", err)
	}
	if accepted != 0 {
		t.Fatalf("accepted=%d, want 0 for an all-unnamed batch", accepted)
	}
	if got := svc.DroppedUnnamed(); got != 5 {
		t.Errorf("dropped=%d after a total drop, want 5 -- the total drop is the exact case 2026-11-02g names", got)
	}
}

// The counter must reach the wire on BOTH surfaces. Beat 790's lesson: adding a
// field is not reporting it, and fixing the JSON endpoint while leaving the HTML
// console unset is the same mistake one surface over.
func TestFleetJSONReportsDroppedUnnamed(t *testing.T) {
	svc := NewService(NewMemoryStore())
	recs := NewMemoryRecordStore(RecordRetention{})
	if err := recs.Append(context.Background(), fleetRec("h1", "r1", time.Now())); err != nil {
		t.Fatalf("seed store: %v", err)
	}
	svc.records = recs
	// Total drop, so the call now refuses (finding 2026-11-02g). The COUNT is the
	// thing under test here, and refusing must not cost it.
	if _, err := svc.IngestActions(context.Background(), schema.Host{ID: "h1"}, schema.Agent{},
		[]schema.Event{{Event: ""}, {Event: ""}}); !errors.Is(err, ErrTotalDrop) {
		t.Fatalf("total-drop IngestActions err = %v, want ErrTotalDrop", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/v1/fleet", nil)
	rec := httptest.NewRecorder()
	svc.handleFleet(rec, req)

	var view fleetView
	if err := json.Unmarshal(rec.Body.Bytes(), &view); err != nil {
		t.Fatalf("decode fleet view: %v", err)
	}
	if view.DroppedUnnamed != 2 {
		t.Errorf("dropped_unnamed = %d on the wire, want 2 -- the count exists but was never reported",
			view.DroppedUnnamed)
	}
}

// A healthy fleet must not grow the field. A zero printed on every page is
// training material for ignoring it (same shape as the always-on rank caveat
// that beat 791's negative control exists to prevent).
func TestFleetJSONOmitsDroppedUnnamedWhenZero(t *testing.T) {
	svc := NewService(NewMemoryStore())
	recs := NewMemoryRecordStore(RecordRetention{})
	if err := recs.Append(context.Background(), fleetRec("h1", "r1", time.Now())); err != nil {
		t.Fatalf("seed store: %v", err)
	}
	svc.records = recs

	req := httptest.NewRequest(http.MethodGet, "/api/v1/fleet", nil)
	rec := httptest.NewRecorder()
	svc.handleFleet(rec, req)

	if got := rec.Body.String(); strings.Contains(got, "dropped_unnamed") {
		t.Errorf("a clean fleet emitted dropped_unnamed; a field that always reads 0 teaches operators to skip it")
	}
}

// The HTML console is a second surface. Beat 790's defect was fixing the JSON
// endpoint and leaving the page silently wrong -- the same mistake, one surface
// over. Both must be asked, separately.
//
// NOTE: this drives the TEMPLATE, not handleFleetPage. renderFleetPageFor builds
// its own `&Service{records: store}` and therefore can never carry a drop count;
// asking it would test a service I did not construct. Rendering the view I did
// construct is the question that matches the cause.
func TestFleetPageReportsDroppedUnnamed(t *testing.T) {
	v := sampleView()
	v.DroppedUnnamed = 3
	page := renderFleetPage(t, v)

	if !strings.Contains(page, `data-testid="dropped-unnamed"`) {
		t.Errorf("the page does not mention 3 discarded events while the JSON endpoint would report "+
			"them -- page excerpt: %s", excerpt(page))
	}
	if !strings.Contains(page, "were received and discarded") {
		t.Errorf("the drop note is present but does not say the events were discarded")
	}
	// M6 failed to fire without this line: asserting that a note EXISTS is not
	// asserting it says the true thing. An operator reading "3 events were
	// discarded" cannot act; "because they carried no event name" is the fact that
	// points at the collector. Third time in four beats that a test asserted
	// presence instead of meaning -- so the cause gets its own assertion.
	if !strings.Contains(page, "carried no event name") {
		t.Errorf("the drop note does not state WHY the events were discarded; without the cause an " +
			"operator knows evidence is gone but not where to look")
	}
	// The note must survive an UNCAPPED view: a drop happens whether or not the
	// scan hit its cap, so tying it to Truncated would hide a small fleet losing
	// records. sampleView is untruncated.
	if f := v.Truncated; f != nil && *f {
		t.Fatalf("premise broken: sampleView is truncated, so this would not prove independence")
	}
}

// Negative control, same reason as the JSON omit test: a caveat printed on a
// clean fleet is a caveat read nowhere.
func TestFleetPageDoesNotReportDroppedUnnamedWhenZero(t *testing.T) {
	page := renderFleetPage(t, sampleView())
	if strings.Contains(page, `data-testid="dropped-unnamed"`) {
		t.Errorf("a clean fleet's page carries a dropped-events warning; that is the always-on caveat " +
			"that teaches operators to ignore it")
	}
}
