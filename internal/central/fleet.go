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
	GeneratedAt time.Time   `json:"generated_at"`
	TotalEvents int         `json:"total_events"`
	Hosts       []fleetHost `json:"hosts"`
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
		_ = json.NewEncoder(w).Encode(fleetView{GeneratedAt: time.Now().UTC(), Hosts: []fleetHost{}})
		return
	}

	q := RecordQuery{
		HostID:      r.URL.Query().Get("host"),
		MinSeverity: r.URL.Query().Get("min_severity"),
		Limit:       atoiDefault(r.URL.Query().Get("limit"), defaultFleetLimit),
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

	view := buildFleetView(recs, recentN)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(view)
}

// buildFleetView rolls records up by host. Records arrive newest-first.
func buildFleetView(recs []Record, recentN int) fleetView {
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
		// Records are newest-first, so the first seen is the latest.
		if h.LastSeen.IsZero() || rec.When.After(h.LastSeen) {
			h.LastSeen = rec.When
		}
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
