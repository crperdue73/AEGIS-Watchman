package collector

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/CRPerdue/agentids/internal/schema"
)

func TestActionsEndpoint(t *testing.T) {
	cases := map[string]string{
		"http://h:8080/api/v1/events": "http://h:8080/api/v1/actions",
		"https://h/api/v1/events":     "https://h/api/v1/actions",
		"http://h:8080/custom":        "http://h:8080/custom",
	}
	for in, want := range cases {
		c := &Client{Endpoint: in}
		if got := c.ActionsEndpoint(); got != want {
			t.Errorf("ActionsEndpoint(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestSubmitActions(t *testing.T) {
	var gotPath string
	var gotBody ActionsRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"accepted":1}`))
	}))
	defer srv.Close()

	c := &Client{Endpoint: srv.URL + "/api/v1/events", HTTP: srv.Client()}
	ev := schema.Event{Event: schema.EventBehaviorRuleMatch, Host: schema.Host{ID: "h1"}}
	if err := c.SubmitActions(context.Background(), ActionsRequest{
		Host: schema.Host{ID: "h1"}, Agent: schema.Agent{ID: "a1"}, Events: []schema.Event{ev},
	}); err != nil {
		t.Fatalf("SubmitActions: %v", err)
	}
	if gotPath != "/api/v1/actions" {
		t.Errorf("path = %q, want /api/v1/actions", gotPath)
	}
	if len(gotBody.Events) != 1 || gotBody.Events[0].Event != schema.EventBehaviorRuleMatch {
		t.Errorf("body events = %#v", gotBody.Events)
	}
}

func TestSubmitActionsEmptyIsNoOp(t *testing.T) {
	called := false
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { called = true }))
	defer srv.Close()
	c := &Client{Endpoint: srv.URL + "/api/v1/events", HTTP: srv.Client()}
	if err := c.SubmitActions(context.Background(), ActionsRequest{Host: schema.Host{ID: "h"}}); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if called {
		t.Error("empty batch should not hit the network")
	}
}

func TestSubmitActionsServerError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	c := &Client{Endpoint: srv.URL + "/api/v1/events", HTTP: srv.Client()}
	err := c.SubmitActions(context.Background(), ActionsRequest{
		Host: schema.Host{ID: "h"}, Events: []schema.Event{{Event: "x"}},
	})
	if err == nil {
		t.Error("expected error on 500")
	}
}
