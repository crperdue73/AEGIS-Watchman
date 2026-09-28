package collector

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/CRPerdue/agentids/internal/mtls"
	"github.com/CRPerdue/agentids/internal/schema"
)

// InventoryRequest is the collector -> central submission body. It matches the
// central service's request shape (kept in sync by a test in the central
// package).
type InventoryRequest struct {
	Host  schema.Host  `json:"host"`
	Agent schema.Agent `json:"agent"`
	Inv   *Inventory   `json:"inventory"`
}

// IngestResult mirrors the central service's response for one submission.
type IngestResult struct {
	FirstSeen bool           `json:"first_seen"`
	Events    []schema.Event `json:"events"`
}

// Client posts inventories to the central service.
type Client struct {
	Endpoint string
	HTTP     *http.Client
	// TLS reports whether this client authenticates with a client cert. Useful
	// for a startup log line so an operator can see the hop is encrypted.
	TLS bool
}

// NewClient returns a client for the given endpoint, with a sane timeout.
func NewClient(endpoint string) *Client {
	return &Client{
		Endpoint: endpoint,
		HTTP:     &http.Client{Timeout: 30 * time.Second},
	}
}

// NewTLSClient returns a client that speaks mutual TLS to the central service.
// It fails fast if the configured certificate material is missing or malformed —
// silently falling back to plaintext would be the worst possible failure mode.
func NewTLSClient(endpoint string, tlsCfg mtls.Config) (*Client, error) {
	cfg, err := tlsCfg.ClientConfig()
	if err != nil {
		return nil, err
	}
	return &Client{
		Endpoint: endpoint,
		HTTP: &http.Client{
			Timeout:   30 * time.Second,
			Transport: &http.Transport{TLSClientConfig: cfg},
		},
		TLS: true,
	}, nil
}

// Submit sends one inventory and returns the central service's result.
func (c *Client) Submit(ctx context.Context, req InventoryRequest) (*IngestResult, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.Endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := c.HTTP.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("post inventory: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("central returned %s", resp.Status)
	}

	var result IngestResult
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode response: %w", err)
	}
	return &result, nil
}
