package central

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"sort"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/provenance"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// maxBodyBytes caps a single inventory submission (1 MiB). A fleet-scale
// inventory is far smaller; this is a guard, not a limit.
const maxBodyBytes = 1 << 20

// InventoryRequest is the collector -> central submission body.
type InventoryRequest struct {
	Host  schema.Host          `json:"host"`
	Agent schema.Agent         `json:"agent"`
	Inv   *collector.Inventory `json:"inventory"`
}

// ActionsRequest is the collector -> central submission body for Layer 3
// behavior events. Separate from InventoryRequest because actions have no
// inventory and no baseline — a collector may submit actions with no file
// change at all (the whole point of matching behavior).
type ActionsRequest struct {
	Host   schema.Host    `json:"host"`
	Agent  schema.Agent   `json:"agent"`
	Events []schema.Event `json:"events"`
}

// Handler returns the central service's HTTP mux.
func (s *Service) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	mux.HandleFunc("/api/v1/events", s.handleIngest)
	mux.HandleFunc("/api/v1/actions", s.handleActions)
	mux.HandleFunc("/api/v1/fleet", s.handleFleet)
	mux.HandleFunc("/api/v1/agents", s.handleAgents)
	mux.HandleFunc("/", s.handleFleetPage)
	return mux
}

// agentFile is one file in a roster entry. `content` is deliberately omitted:
// the roster is identity metadata (path, hash, size, mtime), not persona bodies.
// Central holds those to diff them, but a query endpoint must not become a way
// to read every agent's SOUL.md over the network.
type agentFile struct {
	Path    string    `json:"path"`
	SHA256  string    `json:"sha256"`
	Size    int64     `json:"size"`
	ModTime time.Time `json:"mod_time"`
	// Trust is the Layer 2 verdict for this file: "ok" (listed in the approved
	// manifest, hash matches), "modified" (listed, hash differs), "unsigned"
	// (on host, not listed), or "unverified" when no approval manifest is on
	// record for the root (Layer 2 disabled or the agent is unapproved).
	Trust string `json:"trust"`
}

// agentRosterEntry is one agent's baseline in the roster.
type agentRosterEntry struct {
	HostID string      `json:"host_id"`
	Root   string      `json:"root"`
	Files  []agentFile `json:"files"`
	// Manifest is true when Layer 2 has an approval manifest on record for this
	// root; when false every file's Trust is "unverified".
	Manifest bool `json:"manifest"`
}

// rosterTrust renders the Layer 2 verdict for one baseline file, in the same
// vocabulary the provenance events use ("ok"/"modified"/"unsigned") plus
// "unverified" for a root with no approval manifest on record.
func rosterTrust(m *provenance.Manifest, path, hash string) string {
	if m == nil {
		return "unverified"
	}
	want, listed := m.Files[path]
	switch {
	case !listed:
		return "unsigned"
	case want != hash:
		return "modified"
	default:
		return "ok"
	}
}

// agentsView is the /api/v1/agents response: the fleet's identity roster —
// every agent baseline central holds, with its file hashes. It is the queryable
// half of the brief's fleet-view differentiator ("every agent, its baseline")
// and of Riley's agent-hygiene line ("grep your fleet for someone else's agent
// identity"), where /api/v1/fleet instead sums events.
type agentsView struct {
	GeneratedAt time.Time          `json:"generated_at"`
	TotalAgents int                `json:"total_agents"`
	TotalFiles  int                `json:"total_files"`
	Agents      []agentRosterEntry `json:"agents"`
}

// handleAgents serves GET /api/v1/agents — the fleet identity roster. Query
// params (both optional): host=<id>, root=<path>.
func (s *Service) handleAgents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	view := agentsView{GeneratedAt: time.Now().UTC(), Agents: []agentRosterEntry{}}
	if s.store != nil {
		list, err := s.store.ListBaselines(r.Context())
		if err != nil {
			writeErr(w, http.StatusInternalServerError, "baseline list failed")
			return
		}
		host := r.URL.Query().Get("host")
		root := r.URL.Query().Get("root")
		for _, b := range list {
			if host != "" && b.Key.HostID != host {
				continue
			}
			if root != "" && b.Key.Root != root {
				continue
			}
			entry := agentRosterEntry{HostID: b.Key.HostID, Root: b.Key.Root, Files: []agentFile{}}
			// Layer 2: join the approved manifest for this root, if any, so the
			// roster answers "is every agent's identity approved" and not just
			// "what is it". ForRoot is nil-safe when Layer 2 is disabled.
			manifest, ok := s.manifests.ForRoot(b.Key.Root)
			entry.Manifest = ok
			for _, f := range b.Files {
				entry.Files = append(entry.Files, agentFile{
					Path: f.Path, SHA256: f.SHA256, Size: f.Size, ModTime: f.ModTime,
					Trust: rosterTrust(manifest, f.Path, f.SHA256),
				})
			}
			// Stable file order so the roster is diffable run to run.
			sort.Slice(entry.Files, func(i, j int) bool { return entry.Files[i].Path < entry.Files[j].Path })
			view.Agents = append(view.Agents, entry)
			view.TotalFiles += len(entry.Files)
		}
	}
	view.TotalAgents = len(view.Agents)
	// Stable agent order: host, then root.
	sort.Slice(view.Agents, func(i, j int) bool {
		if view.Agents[i].HostID != view.Agents[j].HostID {
			return view.Agents[i].HostID < view.Agents[j].HostID
		}
		return view.Agents[i].Root < view.Agents[j].Root
	})
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(view)
}

func (s *Service) handleIngest(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes+1))
	if err != nil {
		writeErr(w, http.StatusBadRequest, "unreadable body")
		return
	}
	if len(body) > maxBodyBytes {
		writeErr(w, http.StatusRequestEntityTooLarge, "inventory too large")
		return
	}

	var req InventoryRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid json")
		return
	}
	if req.Inv == nil {
		writeErr(w, http.StatusBadRequest, "missing inventory")
		return
	}
	if req.Host.ID == "" {
		writeErr(w, http.StatusBadRequest, "missing host.id")
		return
	}

	ctx, cancel := contextWithTimeout(r, 10*time.Second)
	defer cancel()

	result, err := s.Ingest(ctx, req.Host, req.Agent, req.Inv)
	if err != nil {
		log.Printf("central: ingest error: %v", err)
		writeErr(w, http.StatusInternalServerError, "ingest failed")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(result)
}

func (s *Service) handleActions(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes+1))
	if err != nil {
		writeErr(w, http.StatusBadRequest, "unreadable body")
		return
	}
	if len(body) > maxBodyBytes {
		writeErr(w, http.StatusRequestEntityTooLarge, "body too large")
		return
	}

	var req ActionsRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid json")
		return
	}
	if req.Host.ID == "" {
		writeErr(w, http.StatusBadRequest, "missing host.id")
		return
	}
	if len(req.Events) == 0 {
		writeErr(w, http.StatusBadRequest, "missing events")
		return
	}

	ctx, cancel := contextWithTimeout(r, 10*time.Second)
	defer cancel()

	n, err := s.IngestActions(ctx, req.Host, req.Agent, req.Events)
	if err != nil {
		log.Printf("central: actions ingest error: %v", err)
		writeErr(w, http.StatusInternalServerError, "ingest failed")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(map[string]int{"accepted": n})
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": msg})
}
