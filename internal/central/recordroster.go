package central

import (
	"context"
	"sort"
	"time"
)

// rosterQuery names the surface buildFleetView actually reads. It exists because
// buildFleetView takes []Record and therefore makes every caller fetch a top-K
// slice -- which is a sample of the fleet, not the fleet.
//
// WHY THIS FILE EXISTS (beat 797, finding 2026-11-03t option 2): the fleet page
// passes the store's 5000 most recent records and rolls them up by host. On a
// fleet whose events concentrate, that is not "the fleet with some detail
// missing" -- it is a host LIST computed from a global recent window, so a quiet
// but critical host can be absent from a page that looks complete. Measured at
// beat 793: 21 hosts, 10,003 events, global cap 5000 -> 10 hosts shown of 21.
//
// The tempting cheap fix (fetch top-3 per host) is WORSE than the bug:
// buildFleetView computes Total, MaxSeverity, LastSeen AND every By* bucket from
// the recs slice, so a top-K slice turns each of those from a fact about the host
// into a fact about its three newest records -- an absent-host bug traded for a
// silently wrong count on every row that IS shown. So RollupByHost supplies
// per-host AGGREGATES computed over all of that host's records, and Recent stays
// exactly what it already is: a bounded detail sample, labelled as such.
//
// SCOPE, stated plainly: aggregates here are exact over the host's RETAINED
// records. They are not a claim about history the store already pruned -- no more
// than TotalEvents was before. The difference is that the bound is now retention,
// an operator-visible setting, rather than a query LIMIT the console never showed.

// rollupByHost walks every record the store holds for the given hosts and
// aggregates by host. An empty hosts slice means every host.
//
// It is a free function over []Record on purpose: all three shipped backends
// (memory, file, sqlite) serve it through Query with Limit 0, which is the
// documented "no limit" spelling. When a SQL backend grows a GROUP BY this can
// become a method without changing any caller's contract -- the interface stays
// the same set of promises, only the cost changes.
func rollupByHost(ctx context.Context, store RecordStore, hosts []string) ([]fleetHost, error) {
	var out []fleetHost
	if store == nil {
		return out, nil
	}
	if len(hosts) == 0 {
		recs, err := store.Query(ctx, RecordQuery{})
		if err != nil {
			return nil, err
		}
		out = aggregateByHost(recs)
		// Ordering matches buildFleetView: worst-first, then busiest.
		sortFleetHosts(out)
		return out, nil
	}
	// Grouped hosts: one bounded query per host. The index exists for exactly
	// this shape -- idx_records_host_when on (host_id, when_unix) -- so each is a
	// seek, not a scan (measured beat 793: 156us per host).
	var all []Record
	for _, h := range hosts {
		recs, err := store.Query(ctx, RecordQuery{HostID: h})
		if err != nil {
			return nil, err
		}
		all = append(all, recs...)
	}
	out = aggregateByHost(all)
	sortFleetHosts(out)
	return out, nil
}

// aggregateByHost computes per-host totals with Recent left empty. Recent is a
// sample and is never derived here; any caller that wants it asks for it, which
// keeps "this row's total" and "these five events" from becoming the same claim.
func aggregateByHost(recs []Record) []fleetHost {
	byHost := map[string]*fleetHost{}
	order := make([]string, 0, len(recs))
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
		// A host's name can arrive on any of its records; keep the first non-empty
		// rather than the newest, so the label does not flap as the stream moves.
		if h.HostName == "" && rec.HostName != "" {
			h.HostName = rec.HostName
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
		if h.LastSeen.IsZero() || rec.When.After(h.LastSeen) {
			h.LastSeen = rec.When
		}
	}
	out := make([]fleetHost, 0, len(order))
	for _, id := range order {
		out = append(out, *byHost[id])
	}
	return out
}

// sortFleetHosts orders hosts worst-first (max severity, then total), the same
// rule buildFleetView applies, so the two surfaces cannot disagree about which
// host a reader must look at first.
func sortFleetHosts(hosts []fleetHost) {
	sort.SliceStable(hosts, func(i, j int) bool {
		ri, rj := severityRank(hosts[i].MaxSeverity), severityRank(hosts[j].MaxSeverity)
		if ri != rj {
			return ri > rj
		}
		return hosts[i].Total > hosts[j].Total
	})
}

// attachRecent fills in each host's detail sample from the per-host Recent path,
// newest first. Kept separate from aggregation so the two limits are independent:
// Recent is capped at recentN and says so, the aggregate is not capped at all.
//
// Errors are returned, not swallowed: a page that silently showed a host with no
// detail would look exactly like a host with no events, which is the class of
// mistake this whole file exists to end.
func attachRecent(ctx context.Context, store RecordStore, hosts []fleetHost, recentN int) ([]fleetHost, error) {
	if store == nil || recentN <= 0 {
		return hosts, nil
	}
	for i := range hosts {
		recs, err := store.Recent(ctx, hosts[i].HostID, recentN)
		if err != nil {
			return hosts, err
		}
		SortRecordsNewestFirst(recs)
		hosts[i].Recent = recs
	}
	return hosts, nil
}

// rosterView assembles a fleetView from per-host aggregates rather than from a
// global record scan. TotalEvents is the sum of the per-host totals, which is a
// fact about the store; Truncated is left nil because nothing was capped.
func rosterView(ctx context.Context, store RecordStore, recentN int, now time.Time) (fleetView, error) {
	hosts, err := rollupByHost(ctx, store, nil)
	if err != nil {
		return fleetView{}, err
	}
	hosts, err = attachRecent(ctx, store, hosts, recentN)
	if err != nil {
		return fleetView{}, err
	}
	view := fleetView{GeneratedAt: now.UTC(), Hosts: hosts}
	if view.Hosts == nil {
		view.Hosts = []fleetHost{}
	}
	for _, h := range view.Hosts {
		view.TotalEvents += h.Total
	}
	return view, nil
}
