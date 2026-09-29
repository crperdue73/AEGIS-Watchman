// Package central — event record store.
//
// The baseline Store answers "what did this agent look like last time". The
// RecordStore answers a different, operator-facing question: "what has this
// fleet actually seen recently?" — the data behind the fleet view (P2.1).
//
// Today central alerts are fire-and-forget: an event goes to a webhook/email
// and is gone. An operator who missed a page, or who joins the system after an
// incident, has nothing to look at. This store is the durable, queryable record
// of detected events, and the read side (`/api/v1/fleet`) is built on it.
//
// It is deliberately small: append a record per detected event, bounded by a
// retention window and a hard cap, queryable by recency and by host. Backends
// follow the same shape as Store (an in-memory reference, a JSON-file default),
// so a SQLite/Postgres implementation slots in later without the service
// learning about it.
package central

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/CRPerdue/agentids/internal/schema"
)

// Record is one detected event as retained for the fleet view. It is a
// denormalised, self-contained summary — a copy of the fields an operator
// triages on — not the full schema.Event, so the store is not coupled to every
// future schema addition and an old record stays readable after a schema
// bump (additive optional fields only).
type Record struct {
	// When is when the record was retained (server time at ingest).
	When time.Time `json:"when"`
	// HostID / AgentRoot attribute the record to an agent.
	HostID    string `json:"host_id"`
	HostName  string `json:"host_name,omitempty"`
	AgentRoot string `json:"agent_root,omitempty"`
	// Event is the event class (identity.drift, behavior.rule_match, ...).
	Event string `json:"event"`
	// Severity is the event severity (info..critical).
	Severity string `json:"severity,omitempty"`
	// RuleID is set for behavior.rule_match events.
	RuleID string `json:"rule_id,omitempty"`
	// Technique is the rule's ATT&CK-style tag, when known.
	Technique string `json:"technique,omitempty"`
	// ActionType is set for behavior events (process.exec, net.egress, ...).
	ActionType string `json:"action_type,omitempty"`
	// Path is set for file-oriented events.
	Path string `json:"path,omitempty"`
	// Summary is a one-line human description.
	Summary string `json:"summary,omitempty"`
}

// RecordQuery filters a record-store lookup. All fields are optional; the zero
// value means "everything retained, newest first, no limit". It is the one
// query shape every backend must serve, so a SQL backend indexes what the
// fleet view and an operator actually ask for — a host, a time range, a
// minimum severity — instead of the caller fetching everything and filtering.
type RecordQuery struct {
	// HostID restricts to one host. Empty = all hosts.
	HostID string
	// Since/Until bound the record time (inclusive Since, exclusive Until).
	// Zero values are unbounded.
	Since time.Time
	Until time.Time
	// MinSeverity excludes records below it (info < low < medium < high <
	// critical). Empty = no floor. Uses the same rank order as severityRank, so
	// an unknown severity is treated as below "info" and excluded when a floor
	// is set.
	MinSeverity string
	// Limit caps the result (0 = no limit), applied after filtering, newest
	// first.
	Limit int
}

// RecordStore persists detected events for the fleet view.
type RecordStore interface {
	// Append retains one record.
	Append(ctx context.Context, rec Record) error
	// Query returns records matching q, newest first.
	Query(ctx context.Context, q RecordQuery) ([]Record, error)
	// Recent returns up to limit records for one host (empty = all), newest
	// first. It is defined in terms of Query so every backend serves the common
	// case through the same path.
	Recent(ctx context.Context, hostID string, limit int) ([]Record, error)
}

// RecordRetention bounds the store: records older than this are dropped on
// write, and no more than MaxRecords are kept. Both are needed — a time window
// keeps the view "recent" and a hard cap bounds memory/disk under a
// high-severity storm.
type RecordRetention struct {
	Window     time.Duration
	MaxRecords int
}

// DefaultRecordRetention is 7 days / 10k records: enough that an operator
// returning Monday sees the weekend, bounded enough to stay a single small
// file in the public repo's dependency-free spirit.
var DefaultRecordRetention = RecordRetention{Window: 7 * 24 * time.Hour, MaxRecords: 10000}

// MemoryRecordStore is the in-memory RecordStore: the reference behavior and
// the backend for tests and -dry-run setups.
type MemoryRecordStore struct {
	mu     sync.RWMutex
	recs   []Record // append order (oldest first)
	retain RecordRetention
	nowFn  func() time.Time
}

// NewMemoryRecordStore returns an empty in-memory record store.
func NewMemoryRecordStore(retain RecordRetention) *MemoryRecordStore {
	if retain.Window <= 0 {
		retain.Window = DefaultRecordRetention.Window
	}
	if retain.MaxRecords <= 0 {
		retain.MaxRecords = DefaultRecordRetention.MaxRecords
	}
	return &MemoryRecordStore{retain: retain, nowFn: time.Now}
}

// Append implements RecordStore.
func (m *MemoryRecordStore) Append(_ context.Context, rec Record) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.recs = append(m.recs, rec)
	m.pruneLocked(m.now())
	return nil
}

// Recent implements RecordStore, defined in terms of Query.
func (m *MemoryRecordStore) Recent(ctx context.Context, hostID string, limit int) ([]Record, error) {
	return m.Query(ctx, RecordQuery{HostID: hostID, Limit: limit})
}

// Query implements RecordStore.
func (m *MemoryRecordStore) Query(_ context.Context, q RecordQuery) ([]Record, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]Record, 0, len(m.recs))
	// Walk newest-first (the slice is oldest-first).
	for i := len(m.recs) - 1; i >= 0; i-- {
		r := m.recs[i]
		if !recordMatches(r, q) {
			continue
		}
		out = append(out, r)
		if q.Limit > 0 && len(out) >= q.Limit {
			break
		}
	}
	return out, nil
}

func (m *MemoryRecordStore) now() time.Time {
	if m.nowFn != nil {
		return m.nowFn()
	}
	return time.Now()
}

// pruneLocked drops records past the retention window and trims to the cap.
// Caller holds m.mu.
func (m *MemoryRecordStore) pruneLocked(now time.Time) {
	cutoff := now.Add(-m.retain.Window)
	// Find the first index still in window (records are roughly sorted by
	// append time; a mis-ordered clock only costs a stale record surviving,
	// never data loss).
	keep := m.recs[:0]
	for _, r := range m.recs {
		if r.When.After(cutoff) || r.When.Equal(cutoff) {
			keep = append(keep, r)
		}
	}
	m.recs = keep
	if len(m.recs) > m.retain.MaxRecords {
		m.recs = m.recs[len(m.recs)-m.retain.MaxRecords:]
	}
}

// FileRecordStore is a durable RecordStore backed by a JSON file, mirroring
// FileStore: atomic writes, no CGO, one small file. It reloads on restart so
// the fleet view survives a central bounce — the whole point of retaining.
type FileRecordStore struct {
	path   string
	mu     sync.RWMutex
	recs   []Record
	retain RecordRetention
	nowFn  func() time.Time
}

type recordStoreDoc struct {
	Version int      `json:"version"`
	Records []Record `json:"records"`
}

const recordStoreVersion = 1

// NewFileRecordStore opens (or creates) a durable record store at path.
func NewFileRecordStore(path string, retain RecordRetention) (*FileRecordStore, error) {
	if retain.Window <= 0 {
		retain.Window = DefaultRecordRetention.Window
	}
	if retain.MaxRecords <= 0 {
		retain.MaxRecords = DefaultRecordRetention.MaxRecords
	}
	f := &FileRecordStore{path: path, retain: retain, nowFn: time.Now}
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return f, nil
		}
		return nil, fmt.Errorf("read record store %q: %w", path, err)
	}
	if len(raw) == 0 {
		return f, nil
	}
	var doc recordStoreDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("parse record store %q: %w", path, err)
	}
	f.recs = doc.Records
	return f, nil
}

// Append implements RecordStore and persists atomically.
func (f *FileRecordStore) Append(_ context.Context, rec Record) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.recs = append(f.recs, rec)
	f.pruneLocked(f.now())
	return f.persistLocked()
}

// Recent implements RecordStore, defined in terms of Query.
func (f *FileRecordStore) Recent(ctx context.Context, hostID string, limit int) ([]Record, error) {
	return f.Query(ctx, RecordQuery{HostID: hostID, Limit: limit})
}

// Query implements RecordStore.
func (f *FileRecordStore) Query(_ context.Context, q RecordQuery) ([]Record, error) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	out := make([]Record, 0, len(f.recs))
	for i := len(f.recs) - 1; i >= 0; i-- {
		r := f.recs[i]
		if !recordMatches(r, q) {
			continue
		}
		out = append(out, r)
		if q.Limit > 0 && len(out) >= q.Limit {
			break
		}
	}
	return out, nil
}

func (f *FileRecordStore) now() time.Time {
	if f.nowFn != nil {
		return f.nowFn()
	}
	return time.Now()
}

func (f *FileRecordStore) pruneLocked(now time.Time) {
	cutoff := now.Add(-f.retain.Window)
	keep := f.recs[:0]
	for _, r := range f.recs {
		if r.When.After(cutoff) || r.When.Equal(cutoff) {
			keep = append(keep, r)
		}
	}
	f.recs = keep
	if len(f.recs) > f.retain.MaxRecords {
		f.recs = f.recs[len(f.recs)-f.retain.MaxRecords:]
	}
}

func (f *FileRecordStore) persistLocked() error {
	doc := recordStoreDoc{Version: recordStoreVersion, Records: f.recs}
	raw, err := json.Marshal(doc)
	if err != nil {
		return fmt.Errorf("marshal record store: %w", err)
	}
	dir := filepath.Dir(f.path)
	if dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o750); err != nil {
			return fmt.Errorf("create record store dir: %w", err)
		}
	}
	tmp, err := os.CreateTemp(dir, filepath.Base(f.path)+".tmp-*")
	if err != nil {
		return fmt.Errorf("create temp record store: %w", err)
	}
	tmpName := tmp.Name()
	defer func() { _ = os.Remove(tmpName) }()
	if _, err := tmp.Write(raw); err != nil {
		tmp.Close()
		return fmt.Errorf("write temp record store: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return fmt.Errorf("sync temp record store: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("close temp record store: %w", err)
	}
	if err := os.Rename(tmpName, f.path); err != nil {
		return fmt.Errorf("rename temp record store: %w", err)
	}
	return nil
}

// RecordFromEvent flattens a schema.Event into a retained Record. It is the
// one place the schema->record mapping lives, so a new event class is added
// here rather than sprinkled through the ingest paths.
func RecordFromEvent(host schema.Host, agent schema.Agent, ev schema.Event, when time.Time) Record {
	rec := Record{
		When:      when,
		HostID:    host.ID,
		HostName:  host.Name,
		AgentRoot: agent.Root,
		Event:     ev.Event,
		Severity:  ev.Severity,
	}
	if rec.HostID == "" {
		rec.HostID = ev.Host.ID // events may carry their own host
	}
	if ev.Rule != nil {
		rec.RuleID = ev.Rule.ID
	}
	if ev.Action != nil {
		rec.ActionType = ev.Action.Type
		if t, ok := ev.Action.Detail["technique"].(string); ok {
			rec.Technique = t
		}
	}
	if ev.File != nil {
		rec.Path = ev.File.Path
	}
	rec.Summary = summarizeEvent(ev)
	return rec
}

func summarizeEvent(ev schema.Event) string {
	where := ev.Host.ID
	if where == "" {
		where = "?"
	}
	switch {
	case ev.Rule != nil && ev.Rule.ID != "":
		line := ev.Rule.ID
		if ev.Action != nil && ev.Action.Type != "" {
			line += " (" + ev.Action.Type + ")"
		}
		return line + " on " + where
	case ev.File != nil && ev.File.Path != "":
		return ev.Event + " " + ev.File.Path
	default:
		return ev.Event + " on " + where
	}
}

// SortRecordsNewestFirst is a helper for callers that receive records from a
// backend not guaranteed ordered (a future SQL backend could return any order).
func SortRecordsNewestFirst(recs []Record) {
	sort.SliceStable(recs, func(i, j int) bool { return recs[i].When.After(recs[j].When) })
}

// recordMatches reports whether r satisfies q's filters. Shared by both
// backends so the query semantics are defined once and a future SQL backend has
// a written reference for what its WHERE clause must mean.
func recordMatches(r Record, q RecordQuery) bool {
	if q.HostID != "" && r.HostID != q.HostID {
		return false
	}
	if !q.Since.IsZero() && r.When.Before(q.Since) {
		return false
	}
	// Until is exclusive so adjacent windows do not double-count an event
	// landing exactly on the boundary.
	if !q.Until.IsZero() && !r.When.Before(q.Until) {
		return false
	}
	if q.MinSeverity != "" && severityRank(r.Severity) < severityRank(q.MinSeverity) {
		return false
	}
	return true
}
