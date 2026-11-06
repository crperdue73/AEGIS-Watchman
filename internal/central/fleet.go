package central

import (
	"encoding/json"
	"net/http"
	"sort"
	"strconv"
	"time"
)

// fleetHost is one host's rollup in the fleet view.
type fleetHost struct {
	HostID      string         `json:"host_id"`
	HostName    string         `json:"host_name,omitempty"`
	Total       int            `json:"total"`
	MaxSeverity string         `json:"max_severity"`
	LastSeen    time.Time      `json:"last_seen"`
	BySeverity  map[string]int `json:"by_severity,omitempty"`
	ByTechnique map[string]int `json:"by_technique,omitempty"`
	// BySource counts records per signal provenance (action_source), so an
	// operator can see how much of a host's story rests on a derived signal
	// ("heuristic") versus an authoritative one.
	BySource map[string]int `json:"by_source,omitempty"`
	// ByPack counts records per rule pack version ("carbonato@7"), so an operator
	// can see at a glance which pack version — and whether one single version —
	// produced a host's alerts. Pack drift across a fleet shows up here.
	ByPack map[string]int `json:"by_pack,omitempty"`
	Recent []Record       `json:"recent,omitempty"`
}

// fleetView is the `/api/v1/fleet` response: the operator's console, rendered
// as data. It answers "which hosts are alerting, and with what" without the
// operator having to grep a log — the deliverable of P2.1's first slice.
type fleetView struct {
	GeneratedAt time.Time `json:"generated_at"`
	// TotalEvents is the number of records this view is computed FROM -- not
	// necessarily the number in the store. Read it together with Truncated.
	TotalEvents int         `json:"total_events"`
	Hosts       []fleetHost `json:"hosts"`
	// Truncated says the scan hit its cap, so every number above describes a
	// window rather than the store. Added beat 790 (finding 2026-11-01n option 1):
	// before this, a capped view was indistinguishable from a complete one --
	// "total_events: 5000" read as a fact about the fleet when it was a fact
	// about a LIMIT clause. The limit is operator-controllable (?limit=), so an
	// operator could also make the console under-report without any indication.
	Truncated *bool `json:"truncated"`

	// ScanLimit echoes the effective cap, because a number an operator cannot see
	// the ceiling of is a number they will read as a total.
	ScanLimit int `json:"scan_limit,omitempty"`

	// DetailCapped says the per-host `Recent` lists were trimmed to a sample.
	//
	// BEAT 799: this is deliberately NOT Truncated, after I wired the fleet page to the
	// roster and discovered the two claims had been sharing one field. Truncated means
	// "every number in this view describes a window, not the store" -- the aggregate is
	// understated. DetailCapped means "the totals are exact, only the five-event detail
	// column is trimmed". The roster page is the second and never the first, so reusing
	// Truncated made the header print "capped at 0 -- this is a sample" over a complete
	// rollup. One flag cannot carry both because an operator's action differs: the first
	// says look again with a bigger limit, the second says the number you are reading is
	// already final. Omitted from JSON when nil so the API does not grow a field for every
	// view; the page always sets it.
	DetailCapped *bool `json:"detail_capped,omitempty"`

	// DroppedUnnamed is the process-lifetime count of events central RECEIVED and
	// discarded for carrying no event name (finding 2026-11-02g, option 3).
	//
	// It is scoped to the view, not the store, and the two are not the same claim:
	// TotalEvents describes records that exist, this describes evidence that never
	// became a record. A non-zero value here means some collector believes central
	// has data that central does not have -- the 202-means-received gap. It is
	// omitted when zero so a healthy fleet's payload does not grow a field saying
	// "nothing went wrong".
	DroppedUnnamed int64 `json:"dropped_unnamed,omitempty"`
}

// defaultFleetLimit caps how many records the view scans. The store is already
// retention-bounded; this is a second guard so a query is cheap.
const defaultFleetLimit = 5000

// handleFleet serves GET /api/v1/fleet — a rollup of recent retained events by
// host. Query params:
//
//	host=<id>    filter to one host
//	limit=<n>    max records to scan (default 5000)
//	recent=<n>   max recent records embedded per host (default 10)
//	since=<RFC3339>    only records at/after this time
//	min_severity=<s>   only records at/above this severity
func (s *Service) handleFleet(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if s.records == nil {
		// No retention configured: the honest answer is an empty view, not a 500.
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(fleetView{
			GeneratedAt:    time.Now().UTC(),
			Hosts:          []fleetHost{},
			DroppedUnnamed: s.DroppedUnnamed(),
		})
		return
	}

	scanLimit := atoiDefault(r.URL.Query().Get("limit"), defaultFleetLimit)
	q := RecordQuery{
		HostID:      r.URL.Query().Get("host"),
		MinSeverity: r.URL.Query().Get("min_severity"),
		// Ask for ONE MORE than the cap. That extra record is how this handler
		// learns whether the cap was actually hit -- a store holding exactly
		// scanLimit records yields scanLimit rows and is complete, while one
		// holding more yields the extra row. Without this probe the two cases are
		// identical from here, and a complete view at the cap would be labelled
		// truncated, which is its own lie.
		Limit: scanLimit + 1,
	}
	if v := r.URL.Query().Get("since"); v != "" {
		t, err := time.Parse(time.RFC3339, v)
		if err != nil {
			writeErr(w, http.StatusBadRequest, "since must be RFC3339 (e.g. 2026-09-29T00:00:00Z)")
			return
		}
		q.Since = t
	}
	recentN := atoiDefault(r.URL.Query().Get("recent"), 10)

	recs, err := s.records.Query(r.Context(), q)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "record query failed")
		return
	}

	// BEAT 800: this endpoint used to roll up `recs` -- the newest scanLimit records -- and call
	// the result the fleet. Beat 799 moved the console onto rosterView, which left the two
	// surfaces answering the same question differently: the page exact, this one understated
	// (fleetcoveragecensus check E measured it and went red, correctly). This is the other half.
	//
	// WHAT IS FIXED BY THIS: at the DEFAULT limit (5000) the totals stop being "the newest N" --
	// every host that has any retained record gets a row with its own exact counts, so a host
	// that went quiet after firing criticals is present instead of absent. That was the defect.
	//
	// WHAT IS NOT FIXED, and must not be claimed: `limit` still bounds the query, and the store
	// applies it BEFORE this function sees a record (eventstore.go:170 breaks out of the scan).
	// So `?limit=5` over a 60-record store still yields totals over five records, and
	// `truncated:true` is TRUE there -- honestly true, at the caller's own request. My first
	// version of this comment said the aggregate was "exact regardless" of limit; that was false
	// the moment an operator passed a small limit, and A2/Z4 measured it within the minute. The
	// comment was corrected, not the tests: an endpoint cannot compute a total over records it
	// deliberately did not read, and pretending otherwise would be the original lie relocated.
	//
	// The real product question -- should a caller-supplied `limit` be able to understate the
	// fleet at all? -- is a behavioural change to a public API and belongs to Dad. Recorded in
	// WORKLOG beat 800 rather than silently decided here.
	//
	// The `recent=<n>` detail sample remains a sample by definition; DetailCapped says so, which
	// keeps "these five events" from being read as "this host's total".
	view := aggregateByHost(recs)
	sortFleetHosts(view)
	// Truncated now means something narrower and true: "the scan that produced this aggregate
	// stopped early", i.e. the query returned more rows than `limit` allowed it to return. It does
	// NOT mean the totals are understated -- they are computed from whatever the query returned,
	// and the query is the bound. Slicing `recs` here would be theatre: I wrote that slice first
	// and it discarded nothing, because the rollup above had already consumed the full slice.
	truncated := len(recs) > scanLimit
	out := fleetView{
		GeneratedAt:    time.Now().UTC(),
		Hosts:          view,
		Truncated:      &truncated,
		ScanLimit:      scanLimit,
		DroppedUnnamed: s.DroppedUnnamed(),
	}
	for _, h := range out.Hosts {
		out.TotalEvents += h.Total
	}
	if out.Hosts == nil {
		out.Hosts = []fleetHost{}
	}

	// The detail sample is per host and goes through each host's own bounded query, the same
	// helper the console uses, so `recent=0` behaves identically on both surfaces.
	//
	// NOTE ON THE REASSIGNMENT (beat 800): attachRecent fills hosts[i].Recent IN PLACE and returns
	// the same backing array. `out.Hosts = filled` therefore assigns a slice to itself. I kept it
	// anyway -- but a mutation proved it is a no-op: deleting the line changed nothing, because
	// the writes had already landed through the slice header. That is worth knowing rather than
	// tidying away: it means this function's correctness depends on attachRecent's in-place
	// semantics, an implementation detail of a different file, and the assignment reads like it is
	// protecting against a copy that never happens.
	if recentN > 0 {
		filled, err := attachRecent(r.Context(), s.records, out.Hosts, recentN)
		if err != nil {
			writeErr(w, http.StatusInternalServerError, "record query failed")
			return
		}
		out.Hosts = filled

		// BEAT 801: set the caveat from what was ACTUALLY trimmed, not from the fact that a
		// sample was requested. Beat 800 set it whenever recentN > 0, so `?recent=100` over a
		// host holding 12 events returned `detail_capped: true` on a complete view -- a flag
		// reporting a trim that did not happen. That is the same species as the bug beat 799
		// fixed one field over (Truncated set over a complete rollup): a caveat that fires
		// regardless of the condition it names trains an operator to ignore it.
		for _, h := range out.Hosts {
			if h.Total > len(h.Recent) {
				detailCapped := true
				out.DetailCapped = &detailCapped
				break
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

// buildFleetView rolls records up by host.
//
// ORDER IS ESTABLISHED HERE, NOT ASSUMED (beat 789). This function used to say "Records arrive
// newest-first" and leave it at that, which made the ordering a promise the caller makes and the
// callee silently depends on. Both shipped backends honour it today (SQLite issues
// `ORDER BY when_unix DESC`), so nothing was broken -- but the one thing that genuinely needs the
// order, Recent below, would break silently on any backend that did not, and the interface's own
// documentation anticipates exactly that backend ("a future SQL backend could return any order").
// The helper written for this case, SortRecordsNewestFirst, had zero callers since it was written.
// Now it has one. Finding 2026-11-01n, option 4.
func buildFleetView(recs []Record, recentN int) fleetView {
	if recentN > 0 {
		// Sorted only when Recent will actually be populated: sorting is the fix for an
		// order-dependent reader, and LastSeen is deliberately order-independent (see
		// lastseen_test.go) so it does not need this. Skipping the sort otherwise keeps the
		// call sites that do not use Recent from paying for it.
		SortRecordsNewestFirst(recs)
	}
	view := fleetView{
		GeneratedAt: time.Now().UTC(),
		TotalEvents: len(recs),
		Hosts:       []fleetHost{},
	}
	byHost := map[string]*fleetHost{}
	order := []string{}
	for _, rec := range recs {
		h, ok := byHost[rec.HostID]
		if !ok {
			h = &fleetHost{
				HostID:      rec.HostID,
				HostName:    rec.HostName,
				BySeverity:  map[string]int{},
				ByTechnique: map[string]int{},
				BySource:    map[string]int{},
				ByPack:      map[string]int{},
			}
			byHost[rec.HostID] = h
			order = append(order, rec.HostID)
		}
		h.Total++
		h.BySeverity[rec.Severity]++
		if rec.Technique != "" {
			h.ByTechnique[rec.Technique]++
		}
		if rec.ActionSource != "" {
			h.BySource[rec.ActionSource]++
		}
		if k := packLabel(rec.RulePack, rec.RuleVersion); k != "" {
			h.ByPack[k]++
		}
		if severityRank(rec.Severity) > severityRank(h.MaxSeverity) {
			h.MaxSeverity = rec.Severity
		}
		// Max, not "first seen": rec.When.After makes this independent of arrival order
		// (asserted in lastseen_test.go). The old comment here claimed the newest-first
		// guarantee was what kept this right, which was backwards -- it is the one reader
		// above that does not care about order, and the comment belonged on Recent.
		if h.LastSeen.IsZero() || rec.When.After(h.LastSeen) {
			h.LastSeen = rec.When
		}
		// The order-dependent reader: keeps the FIRST recentN seen, so it is only correct
		// because of the sort at the top of this function.
		if recentN > 0 && len(h.Recent) < recentN {
			h.Recent = append(h.Recent, rec)
		}
	}
	for _, id := range order {
		view.Hosts = append(view.Hosts, *byHost[id])
	}
	// Order hosts worst-first: by max severity, then by total, so the console
	// leads with what an operator must look at.
	sort.SliceStable(view.Hosts, func(i, j int) bool {
		ri, rj := severityRank(view.Hosts[i].MaxSeverity), severityRank(view.Hosts[j].MaxSeverity)
		if ri != rj {
			return ri > rj
		}
		return view.Hosts[i].Total > view.Hosts[j].Total
	})
	return view
}

// packLabel renders a record's rule pack as "name@version". Either part may be
// absent (an older collector, or a pack with no declared version); the label
// shows whichever is present. Empty when neither is set, so a record with no
// pack never contributes a spurious "" bucket to the rollup.
func packLabel(pack, version string) string {
	switch {
	case pack != "" && version != "":
		return pack + "@" + version
	case pack != "":
		return pack
	case version != "":
		return "@" + version
	default:
		return ""
	}
}

func atoiDefault(s string, def int) int {
	if s == "" {
		return def
	}
	n, err := strconv.Atoi(s)
	if err != nil || n <= 0 {
		return def
	}
	return n
}
