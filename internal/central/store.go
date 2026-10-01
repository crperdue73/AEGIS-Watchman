// Package central implements the central service: baseline store, drift
// computation, alerting, and (later) the Layer 3 rules engine.
//
// Phase 1 scope: accept an agent inventory from a collector, diff it against
// the stored baseline, emit identity.drift events, and return them. Alerts
// (webhook/email) hang off the emitted events.
package central

import (
	"context"
	"errors"
	"sync"

	"github.com/CRPerdue/watchman/internal/collector"
)

// AgentKey uniquely identifies an agent's baseline. Host + agent root, so two
// agents on the same host never collide and one agent moving hosts is legible.
type AgentKey struct {
	HostID string
	Root   string
}

// String renders the key for logs and map keys.
func (k AgentKey) String() string { return k.HostID + ":" + k.Root }

// Store persists the last-known inventory (baseline) per agent.
//
// In-memory today; a SQLite (dev) and Postgres (prod) implementation land in
// Phase 1. The interface exists now so the ingest path never depends on a
// concrete backend — and so tests need no database.
type Store interface {
	// GetBaseline returns the stored inventory for key and whether one exists.
	GetBaseline(ctx context.Context, key AgentKey) (*collector.Inventory, bool, error)
	// PutBaseline overwrites the stored inventory for key.
	PutBaseline(ctx context.Context, key AgentKey, inv *collector.Inventory) error
}

// ErrNoBaseline is returned by callers that require a baseline to exist.
var ErrNoBaseline = errors.New("central: no baseline for agent")

// MemoryStore is a concurrency-safe in-memory Store. Suitable for tests, a
// single-node dev deployment, and as the reference behavior for real backends.
type MemoryStore struct {
	mu    sync.RWMutex
	items map[string]*collector.Inventory
}

// NewMemoryStore returns an empty in-memory store.
func NewMemoryStore() *MemoryStore {
	return &MemoryStore{items: map[string]*collector.Inventory{}}
}

// GetBaseline implements Store.
func (m *MemoryStore) GetBaseline(_ context.Context, key AgentKey) (*collector.Inventory, bool, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	inv, ok := m.items[key.String()]
	if !ok {
		return nil, false, nil
	}
	// Return a copy so callers cannot mutate stored state.
	return cloneInventory(inv), true, nil
}

// PutBaseline implements Store.
func (m *MemoryStore) PutBaseline(_ context.Context, key AgentKey, inv *collector.Inventory) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.items[key.String()] = cloneInventory(inv)
	return nil
}

func cloneInventory(inv *collector.Inventory) *collector.Inventory {
	if inv == nil {
		return nil
	}
	cp := &collector.Inventory{Root: inv.Root, Files: make([]collector.FileState, len(inv.Files))}
	copy(cp.Files, inv.Files)
	return cp
}
