package central

// BEAT 768 -- "No baselines on record" on a page that is allowed to filter.
//
// Beat 767 corrected me: the FLEET page takes no query parameters, so its empty
// state can't be manufactured by a URL. I assumed the roster page was the same.
// It is not. `agentspage.go:36` documents it explicitly:
//
//	// (both optional, same as the JSON endpoint): host=<id>, root=<path>.
//
// and `http.go:125-130` applies those as `continue` filters over ListBaselines.
// So the roster page CAN be filtered -- and its empty branch prints:
//
//	No baselines on record.
//	Either nothing has reported yet -- or Layer 1 has not run.
//
// Both sentences are about the STORE, and both are false when the emptiness came
// from a filter. This is the exact shape I claimed for the fleet page last beat
// and had to retract -- except here it is real.
//
// B1 control: unfiltered page with baselines lists them, no empty sentence
// B2 a host filter that matches nothing over a non-empty store -> which sentence?
// B3 does the same happen on the JSON endpoint? (does the lie need HTML?)
// B4 is the filter combinable with a truthy store (can an operator do it by accident?)
// B5 honesty brake: is the sentence accurate in the one case it was written for?

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
)

func bSeed(t *testing.T, s *Service) {
	t.Helper()
	mod := time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)
	for _, b := range []struct{ host, root string }{
		{"h1", "/home/agent1"},
		{"h2", "/home/agent2"},
	} {
		inv := &collector.Inventory{
			Root: b.root,
			Files: []collector.FileState{
				{Path: "SOUL.md", SHA256: strings.Repeat("a", 64), Size: 10, ModTime: mod},
			},
		}
		if err := s.store.PutBaseline(context.Background(),
			AgentKey{HostID: b.host, Root: b.root}, inv); err != nil {
			t.Fatalf("B: put baseline %s: %v", b.host, err)
		}
	}
}

func bGet(t *testing.T, s *Service, url string) (int, string) {
	t.Helper()
	w := httptest.NewRecorder()
	// Drive the real mux, not the handler: the mux is what decides routing, and
	// beat 767's bug was calling a handler at a path it refuses.
	s.Handler().ServeHTTP(w, httptest.NewRequest(http.MethodGet, url, nil))
	return w.Code, w.Body.String()
}

// B1 -- control: two baselines, no filter, both listed, no empty sentence.
func TestRosterPageListsBaselinesUnfiltered(t *testing.T) {
	s := NewService(NewMemoryStore())
	bSeed(t, s)
	code, body := bGet(t, s, "/agents")
	if code != http.StatusOK {
		t.Fatalf("B1: /agents -> %d", code)
	}
	if !strings.Contains(body, "h1") || !strings.Contains(body, "h2") {
		t.Fatalf("B1: expected both hosts in the roster page")
	}
	if strings.Contains(body, "No baselines on record.") {
		t.Fatalf("B1: empty sentence rendered with two baselines present")
	}
}

// B2 -- THE MEASUREMENT. Filter to a host that has no baseline, over a store that
// has two. What does the page say?
func TestRosterEmptySentenceOverANonEmptyStore(t *testing.T) {
	s := NewService(NewMemoryStore())
	bSeed(t, s)

	code, body := bGet(t, s, "/agents?host=nosuchhost")
	if code != http.StatusOK {
		t.Fatalf("B2: /agents?host=nosuchhost -> %d", code)
	}
	if !strings.Contains(body, "No baselines on record.") {
		t.Fatalf("B2: expected the empty branch; body has no empty sentence. " +
			"If the page now distinguishes 'filtered to nothing' from 'no baselines', " +
			"that is finding 2026-11-02v being addressed -- update this pin.")
	}

	// And the store provably is not empty.
	list, err := s.store.ListBaselines(context.Background())
	if err != nil {
		t.Fatalf("B2: list: %v", err)
	}
	if len(list) != 2 {
		t.Fatalf("B2: store holds %d baselines, expected 2", len(list))
	}
	t.Logf("B2 MEASURED: the store holds %d baselines. GET /agents?host=nosuchhost "+
		"renders 'No baselines on record. Either nothing has reported yet -- or "+
		"Layer 1 has not run.' Both sentences are about the store and both are "+
		"false. A typo in a host id produces a sentence that reads as a "+
		"collection failure.", len(list))
}

// B3 -- does the JSON endpoint make the same claim? (If it doesn't, the defect is
// presentation-only; if it does, it's an API contract.)
func TestRosterJSONMakesTheSameEmptyClaim(t *testing.T) {
	s := NewService(NewMemoryStore())
	bSeed(t, s)

	code, body := bGet(t, s, "/api/v1/agents?host=nosuchhost")
	if code != http.StatusOK {
		t.Fatalf("B3: /api/v1/agents?host=nosuchhost -> %d", code)
	}
	if !strings.Contains(body, `"agents":[]`) {
		t.Fatalf("B3: expected an empty agents array, got %s", body)
	}
	t.Logf("B3 MEASURED: the JSON endpoint returns a bare empty array -- no claim "+
		"about why, which is CORRECT for an API. So this is a presentation defect, "+
		"not an API contract defect. The HTML adds the false explanation; the API "+
		"does not. Body: %s", strings.TrimSpace(body))
}

// B4 -- could an operator hit this without a typo? A filter that USED to match is
// the realistic case: a decommissioned host, or a root that moved.
func TestRosterFilterForADecommissionedHost(t *testing.T) {
	s := NewService(NewMemoryStore())
	bSeed(t, s)

	// The operator bookmarked /agents?host=h3 from when h3 was in the fleet.
	code, body := bGet(t, s, "/agents?host=h3")
	if code != http.StatusOK {
		t.Fatalf("B4: -> %d", code)
	}
	if !strings.Contains(body, "No baselines on record.") {
		t.Fatalf("B4: empty sentence missing; if the page now echoes the filter, " +
			"update this pin and retire finding 2026-11-02v")
	}
	t.Logf("B4 MEASURED: a stale bookmark or a decommissioned host yields the same " +
		"sentence as a broken collector. The page never echoes the filter it " +
		"applied, so the reader has no way to tell the two apart.")
}

// B5 -- the brake: is the sentence honest in the case it was written for?
func TestRosterEmptySentenceIsTrueWithNoStoreRecords(t *testing.T) {
	s := NewService(NewMemoryStore())
	code, body := bGet(t, s, "/agents")
	if code != http.StatusOK {
		t.Fatalf("B5: -> %d", code)
	}
	if !strings.Contains(body, "No baselines on record.") {
		t.Fatalf("B5: empty store should render the empty branch")
	}
	list, err := s.store.ListBaselines(context.Background())
	if err != nil {
		t.Fatalf("B5: list: %v", err)
	}
	if len(list) != 0 {
		t.Fatalf("B5: expected an empty store, got %d", len(list))
	}
	t.Logf("B5 MEASURED HONESTLY: with a genuinely empty store the sentence is TRUE " +
		"and well written. The defect is not the sentence -- it is that the " +
		"{{else}} branch cannot tell WHY it is empty, so it reuses a true " +
		"sentence for a case where it is false.")
}
