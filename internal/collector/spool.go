package collector

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"

	"github.com/CRPerdue/agentids/internal/schema"
)

// Spool is a durable, append-only backlog for Layer 3 matches.
//
// The happy path ships a rule match straight to central. But central is a
// network hop: it can be down, slow, or unreachable when the collector is mid-
// cycle, and a rule match is the one thing in this system you cannot afford to
// drop. "Log and forget" is fine for a debug line; it is not fine for a
// CRITICAL exfil finding.
//
// The spool is the answer. Every match is appended to a JSONL file before the
// network call is even attempted, and only removed once central has accepted
// the batch. On each cycle the collector drains what it can; whatever central
// does not take stays on disk and rides the next attempt. A crash, a restart,
// or a central outage costs latency, never evidence.
//
// Format: one JSON object per line, each a single-event ActionsRequest, so a
// line is independently shippable and a partially-written tail line is
// discarded rather than parsed.
type Spool struct {
	path string
	// maxBytes bounds the file so a long outage cannot fill the disk. When the
	// spool exceeds it, the OLDEST entries are dropped — a home for the newest
	// evidence beats a home for all of it. Zero disables the bound (tests).
	maxBytes int64
}

// DefaultSpoolMaxBytes is the on-disk cap for a single host's spool (16 MiB).
const DefaultSpoolMaxBytes = 16 << 20

// NewSpool opens (creating if needed) a spool at path. A spool with an empty
// path is disabled: Append is a no-op and Drain does nothing. This keeps the
// "spool off" case nil-safe and free of special-casing at call sites.
func NewSpool(path string, maxBytes int64) *Spool {
	if maxBytes <= 0 {
		maxBytes = DefaultSpoolMaxBytes
	}
	return &Spool{path: path, maxBytes: maxBytes}
}

// Enabled reports whether the spool will persist anything.
func (s *Spool) Enabled() bool { return s != nil && s.path != "" }

// Append writes one batch of events to the spool's tail, then trims to the byte
// bound. It is crash-safe by ordering: the write is a single append followed by
// a sync, so a torn write can only ever leave a partial FINAL line, which the
// reader discards.
func (s *Spool) Append(host schema.Host, agent schema.Agent, events []schema.Event) error {
	if !s.Enabled() || len(events) == 0 {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(s.path), 0o750); err != nil {
		return fmt.Errorf("spool: mkdir: %w", err)
	}
	f, err := os.OpenFile(s.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o640)
	if err != nil {
		return fmt.Errorf("spool: open: %w", err)
	}
	defer f.Close()

	rec := spoolRecord{Host: host, Agent: agent, Events: events}
	line, err := json.Marshal(rec)
	if err != nil {
		return fmt.Errorf("spool: marshal: %w", err)
	}
	if _, err := f.Write(append(line, '\n')); err != nil {
		return fmt.Errorf("spool: write: %w", err)
	}
	if err := f.Sync(); err != nil {
		return fmt.Errorf("spool: sync: %w", err)
	}
	return s.trim()
}

// spoolRecord is one spool line: a self-contained, shippable batch.
type spoolRecord struct {
	Host   schema.Host    `json:"host"`
	Agent  schema.Agent   `json:"agent"`
	Events []schema.Event `json:"events"`
}

// Drain hands spooled batches to submit until one fails (central down) or the
// backlog is empty. On success the whole drained prefix is removed atomically
// via a temp-file rewrite; on the first failure the remainder is preserved for
// the next cycle.
//
// Returns the number of events successfully shipped.
func (s *Spool) Drain(ctx context.Context, submit func(context.Context, ActionsRequest) error) (int, error) {
	if !s.Enabled() {
		return 0, nil
	}
	recs, err := s.read()
	if err != nil {
		return 0, err
	}
	if len(recs) == 0 {
		return 0, nil
	}

	shipped := 0
	idx := 0
	for ; idx < len(recs); idx++ {
		r := recs[idx]
		if err := submit(ctx, ActionsRequest{Host: r.Host, Agent: r.Agent, Events: r.Events}); err != nil {
			// Central is unreachable or rejected: keep this and everything
			// after it, stop draining, and let the caller try next cycle.
			log.Printf("collector: spool drain paused at %d/%d: %v", idx, len(recs), err)
			break
		}
		shipped += len(r.Events)
	}
	if shipped == 0 {
		return 0, nil
	}
	if err := s.rewrite(recs[idx:]); err != nil {
		return shipped, err
	}
	return shipped, nil
}

// Len returns the number of spooled batches (test/status helper).
func (s *Spool) Len() (int, error) {
	if !s.Enabled() {
		return 0, nil
	}
	recs, err := s.read()
	return len(recs), err
}

// read parses the spool, discarding a torn final line and any malformed line
// (a corrupt line is dropped rather than wedging the whole backlog).
func (s *Spool) read() ([]spoolRecord, error) {
	f, err := os.Open(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("spool: open: %w", err)
	}
	defer f.Close()

	var out []spoolRecord
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 4<<20)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var r spoolRecord
		if err := json.Unmarshal(line, &r); err != nil {
			log.Printf("collector: spool: dropping malformed line: %v", err)
			continue
		}
		out = append(out, r)
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("spool: scan: %w", err)
	}
	return out, nil
}

// rewrite replaces the spool with the given records via a temp file + rename,
// so a crash mid-rewrite leaves either the old spool or the new one, never a
// half-written mix.
func (s *Spool) rewrite(recs []spoolRecord) error {
	if len(recs) == 0 {
		if err := os.Remove(s.path); err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("spool: remove empty: %w", err)
		}
		return nil
	}
	tmp := s.path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o640)
	if err != nil {
		return fmt.Errorf("spool: temp: %w", err)
	}
	w := bufio.NewWriter(f)
	for _, r := range recs {
		line, err := json.Marshal(r)
		if err != nil {
			f.Close()
			return fmt.Errorf("spool: marshal: %w", err)
		}
		if _, err := w.Write(append(line, '\n')); err != nil {
			f.Close()
			return fmt.Errorf("spool: write: %w", err)
		}
	}
	if err := w.Flush(); err != nil {
		f.Close()
		return fmt.Errorf("spool: flush: %w", err)
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return fmt.Errorf("spool: sync: %w", err)
	}
	if err := f.Close(); err != nil {
		return fmt.Errorf("spool: close: %w", err)
	}
	if err := os.Rename(tmp, s.path); err != nil {
		return fmt.Errorf("spool: rename: %w", err)
	}
	return nil
}

// trim enforces the byte bound by dropping oldest lines until the file fits.
// It reads the current lines, drops a prefix, and rewrites — same crash-safe
// path as drain.
func (s *Spool) trim() error {
	if s.maxBytes <= 0 {
		return nil
	}
	st, err := os.Stat(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("spool: stat: %w", err)
	}
	if st.Size() <= s.maxBytes {
		return nil
	}
	recs, err := s.read()
	if err != nil {
		return err
	}
	// Drop oldest until the serialized size fits. We approximate by dropping
	// from the front; the exact byte count only matters as a bound.
	for len(recs) > 1 {
		size := 0
		for _, r := range recs {
			line, _ := json.Marshal(r)
			size += len(line) + 1
		}
		if int64(size) <= s.maxBytes {
			break
		}
		recs = recs[1:]
	}
	log.Printf("collector: spool over %d bytes; trimming to %d batches", s.maxBytes, len(recs))
	return s.rewrite(recs)
}
