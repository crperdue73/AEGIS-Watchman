package central

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
)

// FileStore is a durable Store backed by a single JSON file. It survives a
// central-service restart, needs no CGO or external database, and stays
// auditable — a deliberate Phase-1 choice over SQLite so the public repo keeps
// a one-dependency footprint. Swap in SQLite/Postgres behind the same Store
// interface when fleet scale demands it.
//
// Writes are atomic (temp file + rename), so a crash mid-write cannot corrupt
// the baseline file.
type FileStore struct {
	path string
	mu   sync.RWMutex
	// items is the in-memory mirror; persisted on every write.
	items map[string]*collector.Inventory
}

// fileStoreDoc is the on-disk JSON shape. Kept versioned so a future migration
// can detect the format.
type fileStoreDoc struct {
	Version int                             `json:"version"`
	Items   map[string]*collector.Inventory `json:"items"`
}

const fileStoreVersion = 1

// NewFileStore opens (or creates) a file store at path, loading any existing
// baselines into memory.
func NewFileStore(path string) (*FileStore, error) {
	fs := &FileStore{path: path, items: map[string]*collector.Inventory{}}

	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return fs, nil // fresh store; created on first write
		}
		return nil, fmt.Errorf("read store %q: %w", path, err)
	}
	if len(raw) == 0 {
		return fs, nil
	}

	var doc fileStoreDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("parse store %q: %w", path, err)
	}
	if doc.Items != nil {
		fs.items = doc.Items
	}
	return fs, nil
}

// GetBaseline implements Store.
func (f *FileStore) GetBaseline(_ context.Context, key AgentKey) (*collector.Inventory, bool, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	inv, ok := f.items[key.String()]
	if !ok {
		return nil, false, nil
	}
	return cloneInventory(inv), true, nil
}

// PutBaseline implements Store and persists to disk atomically.
func (f *FileStore) PutBaseline(_ context.Context, key AgentKey, inv *collector.Inventory) error {
	f.mu.Lock()
	defer f.mu.Unlock()

	f.items[key.String()] = cloneInventory(inv)
	return f.persistLocked()
}

// persistLocked writes the store to disk atomically. Caller holds f.mu.
func (f *FileStore) persistLocked() error {
	doc := fileStoreDoc{Version: fileStoreVersion, Items: f.items}
	raw, err := json.Marshal(doc)
	if err != nil {
		return fmt.Errorf("marshal store: %w", err)
	}

	dir := filepath.Dir(f.path)
	if dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o750); err != nil {
			return fmt.Errorf("create store dir: %w", err)
		}
	}

	tmp, err := os.CreateTemp(dir, filepath.Base(f.path)+".tmp-*")
	if err != nil {
		return fmt.Errorf("create temp store: %w", err)
	}
	tmpName := tmp.Name()
	defer func() { _ = os.Remove(tmpName) }() // no-op if rename succeeded

	if _, err := tmp.Write(raw); err != nil {
		tmp.Close()
		return fmt.Errorf("write temp store: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return fmt.Errorf("sync temp store: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("close temp store: %w", err)
	}
	if err := os.Rename(tmpName, f.path); err != nil {
		return fmt.Errorf("rename temp store: %w", err)
	}
	return nil
}
