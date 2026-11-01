package central

import (
	"html/template"
	"net/http"
	"time"
)

// The roster page is the human face of GET /api/v1/agents — the same plain,
// server-rendered approach as the fleet page: one embedded template, no JS, no
// build step. It renders the exact same view buildAgentsView returns, so the
// page and the endpoint can never disagree; anything the page shows, an
// operator can script. Nothing here reads the store directly.
var agentsPageTmpl = template.Must(template.New("agents").Funcs(template.FuncMap{
	"trustClass": func(s string) string {
		switch s {
		case "ok", "modified", "unsigned", "unverified":
			return "trust-" + s
		default:
			return "trust-unverified"
		}
	},
	"shortHash": func(s string) string {
		// A full 64-char digest is unreadable in a table; the first 12 hex chars
		// are plenty to eyeball-diff two files, and the full hash is a curl away.
		if len(s) <= 12 {
			return s
		}
		return s[:12] + "…"
	},
	"humanBytes": humanBytes,
	"fmtTime":    fmtTime,
}).Parse(agentsHTML))

// handleAgentsPage serves GET /agents — the human identity roster. Query params
// (both optional, same as the JSON endpoint): host=<id>, root=<path>.
func (s *Service) handleAgentsPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if r.URL.Path != "/agents" {
		http.NotFound(w, r)
		return
	}
	view, err := s.buildAgentsView(r.Context(), r.URL.Query().Get("host"), r.URL.Query().Get("root"))
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "baseline list failed")
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := agentsPageTmpl.Execute(w, view); err != nil {
		// Response already partly written; log-only is the only option here.
		if s.logger != nil {
			s.logger.Printf("central: roster page render failed: %v", err)
		}
	}
}

// humanBytes renders a file size in the largest binary unit that keeps the
// integer part under 1024, one decimal. Sizes are small here, so this is
// presentation, not precision.
func humanBytes(n int64) string {
	if n < 0 {
		return "—"
	}
	const unit = 1024
	if n < unit {
		return itoa(int(n)) + " B"
	}
	units := []string{"KiB", "MiB", "GiB", "TiB", "PiB"}
	div, exp := int64(unit), 0
	for m := n / unit; m >= unit && exp < len(units)-1; m /= unit {
		div *= unit
		exp++
	}
	whole := n / div
	frac := (n % div) * 10 / div
	return itoa(int(whole)) + "." + itoa(int(frac)) + " " + units[exp]
}

// fmtTime renders a baseline file's mtime in UTC, or an em dash when unset (a
// zero ModTime is honest — "we do not know" — not a 1970 timestamp).
func fmtTime(t time.Time) string {
	if t.IsZero() {
		return "—"
	}
	return t.UTC().Format("2006-01-02 15:04:05")
}

const agentsHTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>watchman — identity roster</title>
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
  .host { font-weight: 700; color: #e8ebf0; }
  .root { color: #9aa2b1; }
  .hash { color: #6f7686; white-space: nowrap; }
  .empty { padding: 40px 0; color: #7b8291; text-align: center; }
  footer { padding: 14px 24px; color: #5f6673; border-top: 1px solid #262a34;
           font-size: 12px; }
  a { color: #7fb0e8; }
  .trust { font-weight: 700; text-transform: uppercase; font-size: 12px;
           padding: 1px 7px; border-radius: 999px; white-space: nowrap; }
  .trust-ok { background: #17301f; color: #8fe0a8; }
  .trust-modified { background: #472b0f; color: #ffbe6b; }
  .trust-unsigned { background: #4a1220; color: #ff8ba0; }
  .trust-unverified { background: #1b2532; color: #8fb8e0; }
  /* "no manifest" is a dashed outline, not a red pill: an unapproved agent is a
     state to notice, not an alarm — the same read as the derived-signal mark. */
  .manifest-no { border: 1px dashed #6b5340; color: #e0b98f; background: none; }
</style>
</head>
<body>
<header>
  <h1>watchman · identity roster</h1>
  <span class="muted">{{.TotalAgents}} agent(s) · {{.TotalFiles}} file(s)</span>
  <span class="muted" style="margin-left:auto">generated {{.GeneratedAt}}</span>
</header>
<main>
{{if .Agents}}
<table>
  <thead>
    <tr>
      <th>Agent</th><th>Root</th><th>Files</th><th>Manifest</th>
      <th>File</th><th>SHA-256</th><th>Size</th><th>Modified (UTC)</th><th>Trust</th>
    </tr>
  </thead>
  <tbody>
  {{range .Agents}}
    {{if .Files}}
      {{$host := .HostID}}{{$root := .Root}}{{$mf := .Manifest}}{{$n := len .Files}}
      {{range $i, $f := .Files}}
      <tr>
        {{if eq $i 0}}
        <td class="host" rowspan="{{$n}}">{{$host}}</td>
        <td class="root" rowspan="{{$n}}">{{$root}}</td>
        <td rowspan="{{$n}}">{{$n}}</td>
        <td rowspan="{{$n}}">{{if $mf}}yes{{else}}<span class="trust trust-unverified manifest-no">no</span>{{end}}</td>
        {{end}}
        <td>{{$f.Path}}</td>
        <td class="hash" title="{{$f.SHA256}}">{{shortHash $f.SHA256}}</td>
        <td class="muted">{{humanBytes $f.Size}}</td>
        <td class="muted">{{fmtTime $f.ModTime}}</td>
        <td><span class="trust {{trustClass $f.Trust}}">{{$f.Trust}}</span></td>
      </tr>
      {{end}}
    {{else}}
      <tr>
        <td class="host">{{.HostID}}</td>
        <td class="root">{{.Root}}</td>
        <td>0</td>
        <td>{{if .Manifest}}yes{{else}}<span class="trust trust-unverified manifest-no">no</span>{{end}}</td>
        <td colspan="5" class="muted">no files in baseline</td>
      </tr>
    {{end}}
  {{end}}
  </tbody>
</table>
{{else}}
  <div class="empty">
    No baselines on record.<br>
    Either nothing has reported yet — or Layer 1 has not run.
  </div>
{{end}}
</main>
<footer>
  Same data as <a href="/api/v1/agents">/api/v1/agents</a> — machine-readable.
  Back to the <a href="/">fleet view</a>. Refresh for the latest.
</footer>
</body>
</html>
`
