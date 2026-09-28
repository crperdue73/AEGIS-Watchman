package telemetry

import (
	"bytes"
	"io"
	"os"
	"time"

	"github.com/CRPerdue/agentids/internal/rules"
	"github.com/CRPerdue/agentids/internal/schema"
)

// Source is a telemetry input that yields actions for the rules engine.
// Implementations wrap one telemetry backend (auditd file, text stream, eBPF
// later). Keeping this an interface means the collector can grow adapters
// without the engine or the alerting path changing.
type Source interface {
	// Actions returns the batch of actions read on this poll. Implementations
	// are expected to be non-blocking or short-poll: the collector calls this
	// on its tick.
	Actions() ([]Event, error)
}

// Event is one observed action plus its attribution, ready for matching.
type Event struct {
	Action schema.Action
	Actor  *schema.Actor
}

// Engine wires a telemetry Source to the rules engine: read actions, evaluate
// each, return every behavior.rule_match event that fired. This is the seam
// between "what happened on the host" and "what the rule pack says about it".
type Engine struct {
	src    Source
	engine *rules.Engine
	host   schema.Host
	agent  *schema.Agent
	// timeline, when set, remembers each action so correlation rules
	// (prior_event) can see the actions that came before.
	timeline *rules.Timeline
	// Now is the clock used to stamp actions and anchor temporal rules. Nil
	// means time.Now; tests set it for determinism.
	Now func() time.Time
}

// NewEngine builds a telemetry-driven rules engine for one host/agent.
func NewEngine(src Source, eng *rules.Engine, host schema.Host, agent *schema.Agent) *Engine {
	return &Engine{src: src, engine: eng, host: host, agent: agent}
}

// WithTimeline attaches a timeline: every action is recorded into it before
// matching, and correlation rules are evaluated against it.
func (e *Engine) WithTimeline(t *rules.Timeline) *Engine {
	e.timeline = t
	e.engine.WithTimeline(t)
	return e
}

// now returns the engine clock, defaulting to time.Now.
func (e *Engine) now() time.Time {
	if e.Now != nil {
		return e.Now()
	}
	return time.Now()
}

// Matches polls the source once and returns the rule-match events for every
// action that fired at least one rule. Order is deterministic (source order,
// then rule load order).
//
// Actions are recorded into the timeline before matching, so a rule can fire on
// an action whose temporal predicate is satisfied by an EARLIER action in the
// same batch — and a self-referential rule (rare, and usually a bug) will see
// itself. Recording first is the honest choice: the action has happened by the
// time we judge it.
func (e *Engine) Matches() ([]schema.Event, error) {
	events, err := e.src.Actions()
	if err != nil {
		return nil, err
	}
	asOf := e.now()
	var out []schema.Event
	for _, ev := range events {
		if e.timeline != nil {
			e.timeline.Record(e.host.ID, asOf, ev.Action.Type, asOf)
		}
		matched := e.engine.Match(e.host, e.agent, ev.Action, ev.Actor, asOf)
		out = append(out, matched...)
	}
	return out, nil
}

// FileSource reads an auditd log once and is exhausted. A production collector
// would tail the file (offset tracking); for the first cut, reading the whole
// file on each poll with a "seen offset" is out of scope and a re-read of an
// unchanged file simply produces no new matches once dedup lands on central.
//
// To keep v1 honest and simple, FileSource reads from a starting offset and
// remembers where it stopped, so repeated polls continue rather than replay.
type FileSource struct {
	path   string
	offset int64
}

// NewFileSource creates a source that tails an auditd-style log from its
// current end. If the file does not exist, Actions returns an empty batch (a
// host without auditd is not a collector error — it just has no Layer 3 input).
func NewFileSource(path string) *FileSource { return &FileSource{path: path} }

// Actions reads new bytes since the last call and returns the actions they
// encode. Unparsable or incomplete trailing lines are left for the next poll
// (auditd may be mid-write).
func (f *FileSource) Actions() ([]Event, error) {
	fh, err := os.Open(f.path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	defer fh.Close()

	st, err := fh.Stat()
	if err != nil {
		return nil, err
	}
	size := st.Size()
	if size < f.offset {
		// Log rotated/truncated: start over.
		f.offset = 0
	}
	if size == f.offset {
		return nil, nil
	}
	if _, err := fh.Seek(f.offset, io.SeekStart); err != nil {
		return nil, err
	}

	// Read only up to the last newline so we never parse a partial line.
	buf := make([]byte, size-f.offset)
	n, err := io.ReadFull(fh, buf)
	if err != nil && err != io.ErrUnexpectedEOF && err != io.EOF {
		return nil, err
	}
	buf = buf[:n]
	lastNL := -1
	for i := len(buf) - 1; i >= 0; i-- {
		if buf[i] == '\n' {
			lastNL = i
			break
		}
	}
	if lastNL < 0 {
		return nil, nil // no complete line yet
	}
	complete := buf[:lastNL+1]
	f.offset += int64(len(complete))

	recs, err := ParseAuditStream(bytes.NewReader(complete))
	if err != nil {
		return nil, err
	}
	var out []Event
	for _, rec := range recs {
		action, actor, ok := rec.AnyAction()
		if !ok {
			continue
		}
		out = append(out, Event{Action: action, Actor: actor})
	}
	return out, nil
}
