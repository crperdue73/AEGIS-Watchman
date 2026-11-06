package central

import (
	"html/template"
	"net/http"
	"strings"
	"time"
)

// The fleet page is deliberately plain: server-rendered HTML, one embedded
// template, no JS framework, no build step. It renders the exact same rollup as
// GET /api/v1/fleet — the page is a second view of one source of truth, not a
// second implementation. Anything the page can show, the endpoint returns, so an
// operator can script the same console. Polish is v2 (see PHASE1-PLAN "not v1").
// BEAT 790: the header line used to read "N event(s) retained" -- a claim about RETENTION
// computed from a CAPPED SCAN. With the default 5000-record limit and a larger store, the page
// stated as fact a number that was really "the first 5000 I looked at". The JSON view grew a
// `truncated` flag this beat; the page is the surface an operator actually reads, so it must not
// keep asserting the old sentence. (Template comments cannot contain "{{" -- it terminates them
// early, which is why this note lives in Go and not in the template.)
var fleetPageTmpl = template.Must(template.New("fleet").Funcs(template.FuncMap{
	// deref exists because Truncated is a *bool: the pointer lets the JSON encoder
	// distinguish "absent" from "false", but text/template treats ANY non-nil pointer as
	// true -- {{if .Truncated}} would take the truncated branch on a complete view. Measured
	// beat 790, after it printed "sample, not the store's total" over 5 records.
	"deref": func(b *bool) bool { return b != nil && *b },
	"sevClass": func(s string) string {
		switch s {
		case "critical":
			return "sev-critical"
		case "high":
			return "sev-high"
		case "medium":
			return "sev-medium"
		case "low":
			return "sev-low"
		default:
			return "sev-info"
		}
	},
	"join": func(m map[string]int) string {
		if len(m) == 0 {
			return "—"
		}
		// Stable, readable: sort keys so the page is deterministic.
		keys := make([]string, 0, len(m))
		for k := range m {
			keys = append(keys, k)
		}
		sortStrings(keys)
		var b strings.Builder
		for i, k := range keys {
			if i > 0 {
				b.WriteString(", ")
			}
			b.WriteString(k)
			b.WriteString(" ×")
			b.WriteString(itoa(m[k]))
		}
		return b.String()
	},
}).Parse(fleetHTML))

// handleFleetPage serves GET / — the human fleet view.
func (s *Service) handleFleetPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	// BEAT 799: this page used to roll up the store's newest defaultFleetLimit records and print
	// the result as the fleet. Beat 790 fixed the HEADER for that ("scanned, capped at N") but the
	// table underneath was still a host LIST computed from a global recent window -- finding
	// 2026-11-03t, measured at beat 793: 21 hosts, 10,003 events, 10 rows shown, and the 11 missing
	// ones had no marker saying they were missing at all. The caveat sentences beat 792 added were
	// true, and true sentences under an incomplete table are still an incomplete table.
	//
	// The roster supplies exact per-host AGGREGATES (Total, MaxSeverity, LastSeen, every By*
	// bucket) over each host's retained records, and keeps the five-event detail sample a sample.
	// That difference is why the header can now say "retained" again without the beat-790 lie: the
	// number is no longer a restatement of a LIMIT clause.
	view, err := rosterView(r.Context(), s.records, 5, time.Now())
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "record query failed")
		return
	}
	// BEAT 799: DetailCapped, not Truncated. The aggregate here is exact -- every retained record
	// is rolled up -- so Truncated (which means "the numbers describe a window") would be false.
	// What really is trimmed is the five-event detail column, and that is the only caveat the
	// template renders on this path. ScanLimit stays 0: there is no scan limit here, and printing
	// a number would invent one.
	detailCapped := true
	view.DetailCapped = &detailCapped
	view.DroppedUnnamed = s.DroppedUnnamed()
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := fleetPageTmpl.Execute(w, view); err != nil {
		// Response already partly written; log-only is the only option here.
		if s.logger != nil {
			s.logger.Printf("central: fleet page render failed: %v", err)
		}
	}
}

func sortStrings(s []string) {
	// tiny insertion sort — avoids importing sort for a ≤ handful of keys and
	// keeps the template file dependency-free in spirit.
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

const fleetHTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>watchman — fleet</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #12141a; color: #d7dae0;
         font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  header { padding: 18px 24px; border-bottom: 1px solid #262a34;
           display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
  h1 { font-size: 16px; margin: 0; letter-spacing: .04em; }
  .muted { color: #7b8291; }
  main { padding: 20px 24px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #1e222b;
           vertical-align: top; }
  th { color: #9aa2b1; font-weight: 600; font-size: 12px;
       text-transform: uppercase; letter-spacing: .05em; }
  tr:hover td { background: #171a22; }
  .sev { font-weight: 700; text-transform: uppercase; font-size: 12px;
         padding: 1px 7px; border-radius: 999px; white-space: nowrap; }
  .sev-critical { background: #4a1220; color: #ff8ba0; }
  .sev-high { background: #472b0f; color: #ffbe6b; }
  .sev-medium { background: #3a360f; color: #f2e06b; }
  .sev-low { background: #17301f; color: #8fe0a8; }
  .sev-info { background: #1b2532; color: #8fb8e0; }
  /* A derived signal is marked, not hidden: dashed outline reads as
     "inferred, not ground truth" next to a solid severity pill. */
  .derived { font-size: 11px; padding: 0 6px; border-radius: 999px;
             border: 1px dashed #6b5340; color: #e0b98f; white-space: nowrap; }
  .host { font-weight: 700; color: #e8ebf0; }
  .empty { padding: 40px 0; color: #7b8291; text-align: center; }
  ul.recent { margin: 4px 0 0; padding-left: 16px; color: #a9b0be; }
  ul.recent li { margin: 2px 0; }
  footer { padding: 14px 24px; color: #5f6673; border-top: 1px solid #262a34;
           font-size: 12px; }
  a { color: #7fb0e8; }
</style>
</head>
<body>
<header>
  <h1>watchman · fleet view</h1>
  {{if deref .Truncated}}
  <span class="muted">{{.TotalEvents}} event(s) scanned (capped at {{.ScanLimit}} — this is a
    sample, not the store's total)</span>
  {{else}}
  <span class="muted">{{.TotalEvents}} event(s) retained</span>
  {{end}}
  <span class="muted">· {{len .Hosts}} host(s)</span>
  <span class="muted" style="margin-left:auto">generated {{.GeneratedAt}}</span>
</header>
{{if deref .DetailCapped}}
{{/* BEAT 799: the two caveats that used to live here (beat 791 "ranking is over the scanned
     window" and beat 792 "hosts can be missing entirely") described a page that rolled up a
     global newest-first slice. That slide is GONE -- the table is now an exact per-host rollup
     over every retained record -- so those sentences are no longer merely stale, they would be
     libel against a page that is complete. A caveat that outlives its cause teaches an operator
     to ignore caveats, which is the same failure as a permanent "0 dropped".
     What IS still capped is the five-event detail column, and it is capped per host, so it can
     never make a host disappear -- only under-report one host's recent activity. That is the
     only thing left to admit, and it is what this replaces them with. */}}
<div class="derived" role="note" data-testid="detail-capped"
     style="margin:10px 24px">Hosts and their counts are complete: every retained record is
  rolled up. The detail column is capped at the five most recent events <em>per host</em>, so a
  host's row can show fewer recent events than it has — that is the only thing trimmed here.</div>
{{end}}
{{/* BEAT 795 (finding 2026-11-02g option 3): events central RECEIVED and discarded are invisible
     on every surface -- IngestActions skips an unnamed event and returns (0, nil) on a total drop,
     so the collector's 202 means "received", not "stored". This note is deliberately OUTSIDE the
     Truncated block: a drop happens whether or not the scan was capped, and tying it to the cap
     would hide exactly the case where a small fleet silently loses records. It is also silent
     when the count is zero -- a permanent "0 dropped" is training material for ignoring it. */}}
{{if .DroppedUnnamed}}
<div class="derived" role="alert" data-testid="dropped-unnamed"
     style="margin:10px 24px">⚠ {{.DroppedUnnamed}} event(s) were received and discarded because they
  carried no event name. Those events are NOT in this store and NOT in these counts, and the
  collector that sent them was told the batch was accepted.</div>
{{end}}
<main>
{{if .Hosts}}
<table>
  <thead>
    <tr>
      <th>Host</th><th>Events</th><th>Max sev</th><th>Last seen</th>
      <th>By severity</th><th>By technique</th><th>By source</th><th>By pack</th><th>Recent</th>
    </tr>
  </thead>
  <tbody>
  {{range .Hosts}}
    <tr>
      <td class="host">{{.HostID}}{{if .HostName}}<div class="muted">{{.HostName}}</div>{{end}}</td>
      <td>{{.Total}}</td>
      <td><span class="sev {{sevClass .MaxSeverity}}">{{.MaxSeverity}}</span></td>
      <td class="muted">{{.LastSeen}}</td>
      <td class="muted">{{join .BySeverity}}</td>
      <td class="muted">{{join .ByTechnique}}</td>
      <td class="muted">{{join .BySource}}</td>
      <td class="muted">{{join .ByPack}}</td>
      <td>
        <ul class="recent">
        {{range .Recent}}
          <li><span class="sev {{sevClass .Severity}}">{{.Severity}}</span>
            {{if .Summary}}{{.Summary}}{{else if .RuleID}}{{.RuleID}}{{else}}{{.Event}}{{end}}
            {{if .ActionSource}}<span class="derived" title="derived signal — inference, not ground truth">{{.ActionSource}}</span>{{end}}</li>
        {{end}}
        </ul>
      </td>
    </tr>
  {{end}}
  </tbody>
</table>
{{else}}
  <div class="empty">
    No events retained.<br>
    Either the fleet is quiet — or nothing has reported yet.
  </div>
{{end}}
</main>
<footer>
  Same data as <a href="/api/v1/fleet">/api/v1/fleet</a> — machine-readable.
  See the <a href="/agents">identity roster</a>.
  Refresh for the latest.
</footer>
</body>
</html>
`
