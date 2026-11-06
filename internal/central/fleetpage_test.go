package central

// BEAT 765 -- the fleet page had no test that renders it.
//
// Finding 2026-11-02s established that the column headed "Last seen" carries a
// DETECTION time. But the header itself was never asserted anywhere: grep for
// "fleetpage" in *_test.go returned nothing. The page template is a package var
// executed straight to a ResponseWriter, so a header could be renamed, removed,
// or reworded and every suite would stay green.
//
// That is the same defect shape as finding n, one layer out: prose that asserts
// something about behaviour, with no instrument that can go red when the
// behaviour (the rendered output) changes.
//
// Y1  the page renders at all (control -- a panic or template error is not a verdict)
// Y2  every header in the table is present, pinned by name
// Y3  the detection-time column does not present itself as liveness
// Y4  the truncation/empty case still renders a page (an empty console is not a
//     missing console)
// Y5  honesty brake: what can and cannot be asserted about wording

import (
	"bytes"
	"strings"
	"testing"
	"time"
)

func renderFleetPage(t *testing.T, view fleetView) string {
	t.Helper()
	var buf bytes.Buffer
	if err := fleetPageTmpl.Execute(&buf, view); err != nil {
		t.Fatalf("fleet page failed to render: %v", err)
	}
	return buf.String()
}

func sampleView() fleetView {
	when := time.Date(2026, 11, 2, 17, 0, 0, 0, time.UTC)
	return fleetView{
		GeneratedAt: when,
		TotalEvents: 2,
		Hosts: []fleetHost{{
			HostID:      "h1",
			HostName:    "box",
			Total:       2,
			MaxSeverity: "high",
			LastSeen:    when,
			BySeverity:  map[string]int{"high": 2},
			ByTechnique: map[string]int{},
			BySource:    map[string]int{},
			ByPack:      map[string]int{},
		}},
	}
}

// Y1 -- control. If the template can't execute, nothing below means anything.
func TestFleetPageRenders(t *testing.T) {
	html := renderFleetPage(t, sampleView())
	if len(html) < 200 {
		t.Fatalf("Y1: rendered page is only %d bytes -- the template produced "+
			"almost nothing, so the header assertions below would be vacuous",
			len(html))
	}
	if !strings.Contains(html, "h1") {
		t.Fatalf("Y1: rendered page does not contain the host id; the fixture and " +
			"the template disagree about field names")
	}
}

// Y2 -- every header, pinned. Adding or removing a column should force a decision.
func TestFleetPageHeadersArePinned(t *testing.T) {
	html := renderFleetPage(t, sampleView())
	want := []string{
		"<th>Host</th>",
		"<th>Events</th>",
		"<th>Max sev</th>",
		"<th>Last seen</th>",
		"<th>By severity</th>",
		"<th>By technique</th>",
		"<th>By source</th>",
		"<th>By pack</th>",
		"<th>Recent</th>",
	}
	for _, h := range want {
		if !strings.Contains(html, h) {
			t.Fatalf("Y2: header %q is gone from the fleet page. If that is the "+
				"intent, update this pin with the new wording -- a column that "+
				"vanishes without a decision is the failure mode this test exists "+
				"to prevent.", h)
		}
	}
}

// Y3 -- the finding itself, as a test.
//
// This asserts the NEGATIVE: the column that shows a detection time must not
// claim liveness. It is intentionally worded so that a rename to the honest
// label ("Last detection") does NOT satisfy it either -- changing the label is a
// decision, so it must come with an edit to this test. What this catches is
// drift toward MORE misleading wording.
func TestLastSeenColumnDoesNotClaimLiveness(t *testing.T) {
	html := renderFleetPage(t, sampleView())

	// Measured today: the header says "Last seen".
	if !strings.Contains(html, "<th>Last seen</th>") {
		t.Fatalf("Y3: the column is no longer headed \"Last seen\". If it was " +
			"renamed to something honest like \"Last detection\", that is finding " +
			"2026-11-02s being fixed -- update this pin and retire the finding.")
	}

	// The misreading is what matters, so pin the absence of the words that would
	// make it worse. A future edit that relabels this "Last contact" or "Last
	// heartbeat" makes the defect LIVE rather than latent, and should be caught.
	for _, worse := range []string{"<th>Last contact</th>", "<th>Last heartbeat</th>", "<th>Status</th>"} {
		if strings.Contains(html, worse) {
			t.Fatalf("Y3: the detection-time column is now headed %q -- that "+
				"presents it as liveness, which finding 2026-11-02s measured it "+
				"CANNOT mean (a clean ingest stores no records).", worse)
		}
	}
	t.Logf("Y3 MEASURED: header is \"Last seen\" and the value is max(retention " +
		"time) = last ingest that DETECTED. The misreading is real; the fix is a " +
		"wording decision that has not been made.")
}

// Y4 -- an empty fleet still renders. A page that errors on zero hosts hides the
// console exactly when an operator is trying to confirm it is working.
func TestEmptyFleetStillRenders(t *testing.T) {
	html := renderFleetPage(t, fleetView{
		GeneratedAt: time.Date(2026, 11, 2, 17, 0, 0, 0, time.UTC),
		Hosts:       []fleetHost{},
	})
	if len(html) < 200 {
		t.Fatalf("Y4: empty fleet rendered only %d bytes", len(html))
	}
	if strings.Contains(html, "<th>Host</th>") {
		t.Fatalf("Y4: the empty fleet rendered the table header with no rows -- " +
			"the template's {{if .Hosts}} guard changed")
	}
}

// Y5 -- what this test cannot do.
func TestFleetPageWordingBrake(t *testing.T) {
	// There is no way to assert "this label is understood correctly". Y3 pins the
	// current string and blocks the specific worse strings; anything beyond that
	// is a human decision about wording. Recording the limit rather than implying
	// the page is now verified.
	t.Logf("Y5 MEASURED: these pins catch REMOVAL and DRIFT toward worse wording. " +
		"They cannot judge whether \"Last seen\" is read correctly by an operator. " +
		"Only a wording decision closes finding 2026-11-02s.")
}
