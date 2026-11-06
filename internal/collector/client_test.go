package collector

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/mtls"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
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

// TestSubmitSendsInventoryAndDecodesResult covers Client.Submit, which had NO test at all --
// client_test.go only exercised SubmitActions. Submit is the path every inventory takes to central,
// and four of its error arms (client.go:69/:73/:79/:89) were listed as mutation survivors precisely
// because nothing ever called it.
//
// The happy path matters for a reason beyond coverage: it pins that the request is a POST carrying
// the marshalled InventoryRequest and that a 2xx body is decoded into IngestResult. If the transport
// quietly became a GET, or the result stopped being decoded, a collector would keep reporting
// success while central recorded nothing -- the silent-blackout failure this product exists to catch.
func TestSubmitSendsInventoryAndDecodesResult(t *testing.T) {
	var gotMethod, gotCT string
	var gotBody InventoryRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotCT = r.Header.Get("Content-Type")
		if err := json.NewDecoder(r.Body).Decode(&gotBody); err != nil {
			t.Errorf("central could not decode the request: %v", err)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(IngestResult{FirstSeen: true})
	}))
	defer srv.Close()

	c := NewClient(srv.URL)
	res, err := c.Submit(context.Background(), InventoryRequest{
		Host:  schema.Host{ID: "h-1"},
		Agent: schema.Agent{ID: "a-1"},
	})
	if err != nil {
		t.Fatalf("Submit: %v", err)
	}
	if gotMethod != http.MethodPost {
		t.Errorf("method = %s, want POST -- an inventory that arrives as a GET is dropped without an error", gotMethod)
	}
	if gotCT != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", gotCT)
	}
	if gotBody.Host.ID != "h-1" || gotBody.Agent.ID != "a-1" {
		t.Errorf("central received host=%q agent=%q, want h-1/a-1", gotBody.Host.ID, gotBody.Agent.ID)
	}
	if res == nil || !res.FirstSeen {
		t.Errorf("IngestResult not decoded into the return value: %+v -- FirstSeen=false means a "+
			"first sighting of a host would be reported as a repeat", res)
	}
}

// TestSubmitRejectsNon2xx pins the status arms at client.go:84 (inventory) and client.go:141
// (actions). Both are `if resp.StatusCode < 200 || resp.StatusCode >= 300` and both were mutation
// survivors: `<` -> `<=` at both sites.
//
// Why the boundaries and not just a 500: the interesting inputs are 199, 200, 299, 300. A status
// check that is off by one turns either a 199 (informational, no useful body -- must fail) or a 300
// (multiple choices, a redirect body that is not an IngestResult -- must fail) into a success. The
// 300 case is the dangerous one: some proxies answer 300 for an ambiguous request, the body decodes
// into a zero-valued IngestResult, and the collector reports a successful sync that never happened.
func TestSubmitRejectsNon2xx(t *testing.T) {
	cases := []struct {
		status  int
		wantErr bool
		why     string
	}{
		// 1xx is NOT testable through net/http: the client treats it as informational and waits
		// for the final response, so a handler's WriteHeader(199) never reaches the status check.
		// Measured at beat 822 with a probe -- the server was hit and the call returned nil. A row
		// asserting 199 here would be asserting a fiction about net/http.
		{200, false, "lower bound is inclusive"},
		// 204 is a valid 2xx by the status check, and Submit then fails decoding an empty body.
		// That is the honest behaviour and it is asserted separately below: a 204 means central
		// accepted and said nothing, which Submit cannot represent because its contract is to
		// return the decoded IngestResult.
		{299, false, "upper bound is inclusive"},
		{300, true, "a redirect body is not an IngestResult; accepting it reports a sync that never happened"},
		{400, true, "client error"},
		{500, true, "server error"},
	}
	for _, tc := range cases {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(tc.status)
			if tc.status == 200 || tc.status == 299 {
				json.NewEncoder(w).Encode(IngestResult{FirstSeen: true})
			}
		}))
		c := NewClient(srv.URL)
		_, err := c.Submit(context.Background(), InventoryRequest{Host: schema.Host{ID: "h-1"}})
		if tc.wantErr && err == nil {
			t.Errorf("status %d: Submit returned nil error; %s", tc.status, tc.why)
		}
		if !tc.wantErr && err != nil {
			t.Errorf("status %d: Submit failed but %s: %v", tc.status, tc.why, err)
		}
		srv.Close()
	}
}

// TestSubmitTransportErrorIsReturned pins client.go:69 (`if err != nil` around HTTP.Do). A client
// that swallows a transport failure and returns an empty result would make an unreachable central
// service look like a clean sync.
func TestSubmitTransportErrorIsReturned(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	url := srv.URL
	srv.Close() // now nothing is listening

	c := NewClient(url)
	if _, err := c.Submit(context.Background(), InventoryRequest{Host: schema.Host{ID: "h-1"}}); err == nil {
		t.Fatal("Submit returned nil for an unreachable central service -- an outage would look like a successful sync")
	}
}

// TestSubmitActionsRejectsNon2xx pins client.go:141 on the actions path, which the existing
// server-error test only exercised at 500. See TestSubmitRejectsNon2xx for why 300 is the case
// that matters.
func TestSubmitActionsRejectsNon2xx(t *testing.T) {
	for _, tc := range []struct {
		status  int
		wantErr bool
	}{
		// 1xx dropped: net/http consumes it as informational (see TestSubmitRejectsNon2xx).
		{200, false},
		{299, false},
		{300, true},
	} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(tc.status)
		}))
		c := NewClient(srv.URL)
		// Events must be non-empty: SubmitActions short-circuits to nil for an empty batch
		// (client.go:123), so an empty request would "pass" this test without ever reaching the
		// status check. Caught at beat 822 when the 300 row reported nil against code that
		// correctly rejects 300 -- the test, not the client, was wrong.
		err := c.SubmitActions(context.Background(), ActionsRequest{
			Host:   schema.Host{ID: "h-1"},
			Events: []schema.Event{{}},
		})
		if tc.wantErr && err == nil {
			t.Errorf("actions status %d: nil error, want failure", tc.status)
		}
		if !tc.wantErr && err != nil {
			t.Errorf("actions status %d: unexpected error %v", tc.status, err)
		}
		srv.Close()
	}
}

// TestSubmitNoContentIsAnErrorForTheCallerNotASilentSuccess pins the one status a reasonable
// reader might expect to succeed: 204. Net/http gives us an empty body, the status check passes
// (204 is 2xx), and the JSON decode fails with EOF.
//
// That is the correct outcome, but the REASON is worth stating so nobody "fixes" it later by
// returning an empty IngestResult on EOF: Submit's contract is to return what central said, and
// "nothing" is not a claim about first-seen state. An empty result would mean every host reports
// FirstSeen=false, i.e. every genuine first sighting is silently downgraded to a repeat.
func TestSubmitNoContentIsAnErrorForTheCaller(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	c := NewClient(srv.URL)
	res, err := c.Submit(context.Background(), InventoryRequest{Host: schema.Host{ID: "h-1"}})
	if err == nil {
		t.Fatalf("Submit accepted a 204 and returned %+v; a body-less success must not be "+
			"presented as a decoded result", res)
	}
}

// TestNewTLSClientFailsFastOnBadMaterial pins client.go:53 -- the mTLS fail-fast.
//
// The function comment states the stake in its own words: "It fails fast if the configured
// certificate material is missing or malformed -- silently falling back to plaintext would be the
// worst possible failure mode." An agent that ships file inventories, process state and behavior
// events to central, but quietly does so in the clear because a cert path had a typo, is the exact
// failure this check exists to prevent. The mutant `err != nil` -> `err == nil` makes the collector
// IGNORE the config error and return a client built from a nil tls.Config -- a plaintext client
// reporting success.
//
// It was a survivor until beat 822 because nothing ever called NewTLSClient with bad material.
func TestNewTLSClientFailsFastOnBadMaterial(t *testing.T) {
	cases := []struct {
		name string
		cfg  mtls.Config
		why  string
	}{
		{
			name: "nothing configured",
			cfg:  mtls.Config{},
			why:  "no cert, key or CA at all",
		},
		{
			name: "cert path that does not exist",
			cfg: mtls.Config{
				CertFile: "/nonexistent/tls.crt",
				KeyFile:  "/nonexistent/tls.key",
				CAFile:   "/nonexistent/ca.crt",
			},
			why: "a typo'd certificate path must not degrade to plaintext",
		},
	}
	for _, tc := range cases {
		c, err := NewTLSClient("https://central.example/api/v1/events", tc.cfg)
		if err == nil {
			t.Errorf("%s: NewTLSClient returned a client and no error; %s. An agent that ships "+
				"inventories in the clear because a cert path was wrong is the failure mode this "+
				"check exists to stop (got client=%+v)", tc.name, tc.why, c)
		}
		if c != nil {
			t.Errorf("%s: NewTLSClient returned a non-nil client alongside an error; a caller "+
				"that ignores err would then submit over an unconfigured transport", tc.name)
		}
	}
}
