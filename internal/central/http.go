package central

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/CRPerdue/agentids/internal/collector"
	"github.com/CRPerdue/agentids/internal/schema"
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

// Handler returns the central service's HTTP mux.
func (s *Service) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	mux.HandleFunc("/api/v1/events", s.handleIngest)
	return mux
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

func writeErr(w http.ResponseWriter, code int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": msg})
}
