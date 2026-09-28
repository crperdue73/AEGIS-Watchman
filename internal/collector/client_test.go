package collector

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/CRPerdue/agentids/internal/schema"
)

func TestClientSubmit(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("method = %s", r.Method)
		}
		if r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("content-type = %q", r.Header.Get("Content-Type"))
		}
		var req InventoryRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Errorf("decode: %v", err)
		}
		if req.Host.ID != "h-1" {
			t.Errorf("host id = %q", req.Host.ID)
		}
		w.WriteHeader(http.StatusAccepted)
		_ = json.NewEncoder(w).Encode(IngestResult{
			FirstSeen: false,
			Events: []schema.Event{{
				Schema:   schema.Version,
				Event:    schema.EventIdentityDrift,
				Severity: schema.SeverityHigh,
			}},
		})
	}))
	defer srv.Close()

	c := NewClient(srv.URL)
	res, err := c.Submit(context.Background(), InventoryRequest{
		Host:  schema.Host{ID: "h-1"},
		Agent: schema.Agent{ID: "a-1", Root: "/x"},
		Inv:   &Inventory{Root: "/x", Files: []FileState{{Path: "SOUL.md", SHA256: "aaa", Size: 1}}},
	})
	if err != nil {
		t.Fatalf("Submit: %v", err)
	}
	if len(res.Events) != 1 || res.Events[0].Severity != schema.SeverityHigh {
		t.Errorf("unexpected result: %+v", res)
	}
}

func TestClientSubmitErrorsOnNon2xx(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	c := NewClient(srv.URL)
	_, err := c.Submit(context.Background(), InventoryRequest{
		Host: schema.Host{ID: "h-1"},
		Inv:  &Inventory{Root: "/x"},
	})
	if err == nil {
		t.Error("expected error on 500 response")
	}
}
