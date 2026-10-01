package central

import (
	"html/template"
	"net/http"
	"strings"
)

// The fleet page is deliberately plain: server-rendered HTML, one embedded
// template, no JS framework, no build step. It renders the exact same rollup as
// GET /api/v1/fleet — the page is a second view of one source of truth, not a
// second implementation. Anything the page can show, the endpoint returns, so an
// operator can script the same console. Polish is v2 (see PHASE1-PLAN "not v1").
var fleetPageTmpl = template.Must(template.New("fleet").Funcs(template.FuncMap{
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
	var recs []Record
	if s.records != nil {
		got, err := s.records.Recent(r.Context(), "", defaultFleetLimit)
		if err != nil {
			writeErr(w, http.StatusInternalServerError, "record query failed")
			return
		}
		recs = got
	}
	view := buildFleetView(recs, 5)
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
  <span class="muted">{{.TotalEvents}} event(s) retained
    · {{len .Hosts}} host(s)</span>
  <span class="muted" style="margin-left:auto">generated {{.GeneratedAt}}</span>
</header>
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
  Refresh for the latest.
</footer>
</body>
</html>
`
