//go:build sqlite

package central

import (
	"context"
	"database/sql"
	"fmt"
	"path/filepath"
	"time"

	_ "modernc.org/sqlite" // pure-Go driver: no CGO, so `go build` works anywhere
)

// SQLiteRecordStore is a durable RecordStore backed by SQLite (WAL mode). It is
// built only with `-tags sqlite`, so the default binary keeps its one-dependency
// footprint — the same opt-in shape as the eBPF adapter behind `-tags ebpf`.
//
// Why this exists (see docs/P2.1C-RECORD-STORE-BACKENDS.md): the JSON
// FileRecordStore rewrites the whole file per append — O(n) per event, worst
// during the incident this tool exists for — assumes a single writer, and has no
// index. SQLite fixes all three: append is WAL-cheap, the database locks across
// processes, and (host_id, when) / severity_rank are indexed so Query is a range
// scan, not a full read.
//
// The driver is modernc.org/sqlite (pure Go). mattn/go-sqlite3 is faster but
// needs CGO, which would break "clone and build" on a box with no C compiler.
type SQLiteRecordStore struct {
	db     *sql.DB
	retain RecordRetention
}

// NewSQLiteRecordStore opens (or creates) a SQLite record store at path and
// applies the schema. The directory is created if missing.
func NewSQLiteRecordStore(path string, retain RecordRetention) (*SQLiteRecordStore, error) {
	if retain.Window <= 0 {
		retain.Window = DefaultRecordRetention.Window
	}
	if retain.MaxRecords <= 0 {
		retain.MaxRecords = DefaultRecordRetention.MaxRecords
	}
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := mkdirAll(dir); err != nil {
			return nil, err
		}
	}
	// WAL + a busy timeout so a second writer waits rather than failing — the
	// cross-process safety the JSON file lacks.
	dsn := path + "?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=synchronous(NORMAL)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open sqlite record store %q: %w", path, err)
	}
	// SQLite is a single-writer store; a small pool avoids lock churn while
	// still letting readers run concurrently under WAL.
	db.SetMaxOpenConns(4)
	s := &SQLiteRecordStore{db: db, retain: retain}
	if err := s.migrate(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *SQLiteRecordStore) migrate() error {
	const schemaSQL = `
CREATE TABLE IF NOT EXISTS records (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  when_unix    INTEGER NOT NULL,   -- record time, unix seconds (UTC)
  host_id      TEXT    NOT NULL,
  host_name    TEXT    NOT NULL DEFAULT '',
  agent_root   TEXT    NOT NULL DEFAULT '',
  event        TEXT    NOT NULL DEFAULT '',
  severity     TEXT    NOT NULL DEFAULT '',
  severity_rank INTEGER NOT NULL DEFAULT 0,
  rule_id      TEXT    NOT NULL DEFAULT '',
  technique    TEXT    NOT NULL DEFAULT '',
  rule_pack    TEXT    NOT NULL DEFAULT '',
  rule_version TEXT    NOT NULL DEFAULT '',
  action_type  TEXT    NOT NULL DEFAULT '',
  action_source TEXT   NOT NULL DEFAULT '',
  path         TEXT    NOT NULL DEFAULT '',
  summary      TEXT    NOT NULL DEFAULT ''
);
-- The fleet view's primary question: this host, newest first.
CREATE INDEX IF NOT EXISTS idx_records_host_when ON records (host_id, when_unix);
-- A severity floor is a range scan over the rank.
CREATE INDEX IF NOT EXISTS idx_records_sev ON records (severity_rank);
`
	if _, err := s.db.Exec(schemaSQL); err != nil {
		return fmt.Errorf("sqlite record store migrate: %w", err)
	}
	// CREATE TABLE IF NOT EXISTS does not evolve a table created by an earlier
	// build, so an additive column is added explicitly. Additive-only: the store
	// never drops or retypes a column (matching the event schema's policy).
	if err := s.ensureColumn("records", "action_source", "TEXT NOT NULL DEFAULT ''"); err != nil {
		return err
	}
	if err := s.ensureColumn("records", "rule_pack", "TEXT NOT NULL DEFAULT ''"); err != nil {
		return err
	}
	if err := s.ensureColumn("records", "rule_version", "TEXT NOT NULL DEFAULT ''"); err != nil {
		return err
	}
	return nil
}

// ensureColumn adds col to table if it is not already present. Idempotent, so
// it is safe to run on every open.
func (s *SQLiteRecordStore) ensureColumn(table, col, decl string) error {
	rows, err := s.db.Query(`PRAGMA table_info(` + table + `)`)
	if err != nil {
		return fmt.Errorf("sqlite record store pragma %s: %w", table, err)
	}
	defer rows.Close()
	for rows.Next() {
		var (
			cid     int
			name    string
			ctype   string
			notnull int
			dflt    sql.NullString
			pk      int
		)
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			return fmt.Errorf("sqlite record store pragma scan: %w", err)
		}
		if name == col {
			return nil // already present
		}
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("sqlite record store pragma iterate: %w", err)
	}
	if _, err := s.db.Exec(`ALTER TABLE ` + table + ` ADD COLUMN ` + col + ` ` + decl); err != nil {
		return fmt.Errorf("sqlite record store add column %s.%s: %w", table, col, err)
	}
	return nil
}

// Append implements RecordStore: one INSERT, then retention pruning.
func (s *SQLiteRecordStore) Append(ctx context.Context, rec Record) error {
	const ins = `INSERT INTO records
  (when_unix, host_id, host_name, agent_root, event, severity, severity_rank,
   rule_id, technique, rule_pack, rule_version, action_type, action_source, path, summary)
  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`
	_, err := s.db.ExecContext(ctx, ins,
		rec.When.Unix(), rec.HostID, rec.HostName, rec.AgentRoot, rec.Event,
		rec.Severity, severityRank(rec.Severity), rec.RuleID, rec.Technique,
		rec.RulePack, rec.RuleVersion, rec.ActionType, rec.ActionSource, rec.Path, rec.Summary,
	)
	if err != nil {
		return fmt.Errorf("sqlite record store append: %w", err)
	}
	return s.prune(ctx)
}

// prune enforces the retention window and the hard cap. Both are needed: the
// window keeps the view "recent", the cap bounds a severity storm.
func (s *SQLiteRecordStore) prune(ctx context.Context) error {
	// Window: delete anything older than the cutoff. Unix seconds, so this is
	// an indexed range delete.
	if s.retain.Window > 0 {
		cutoff := s.nowUnix() - int64(s.retain.Window.Seconds())
		if _, err := s.db.ExecContext(ctx, `DELETE FROM records WHERE when_unix < ?`, cutoff); err != nil {
			return fmt.Errorf("sqlite record store prune window: %w", err)
		}
	}
	// Cap: keep the newest MaxRecords. Delete the oldest beyond that.
	if s.retain.MaxRecords > 0 {
		const trim = `DELETE FROM records WHERE id NOT IN (
  SELECT id FROM records ORDER BY when_unix DESC, id DESC LIMIT ?
)`
		if _, err := s.db.ExecContext(ctx, trim, s.retain.MaxRecords); err != nil {
			return fmt.Errorf("sqlite record store prune cap: %w", err)
		}
	}
	return nil
}

// Query implements RecordStore. The WHERE clause is the SQL spelling of
// recordMatches: host equality, Since inclusive, Until exclusive, a severity
// floor by rank.
func (s *SQLiteRecordStore) Query(ctx context.Context, q RecordQuery) ([]Record, error) {
	sqlStr := `SELECT when_unix, host_id, host_name, agent_root, event, severity,
  rule_id, technique, rule_pack, rule_version, action_type, action_source, path, summary FROM records WHERE 1=1`
	var args []any
	if q.HostID != "" {
		sqlStr += " AND host_id = ?"
		args = append(args, q.HostID)
	}
	if !q.Since.IsZero() {
		sqlStr += " AND when_unix >= ?"
		args = append(args, q.Since.Unix())
	}
	if !q.Until.IsZero() {
		sqlStr += " AND when_unix < ?"
		args = append(args, q.Until.Unix())
	}
	if q.MinSeverity != "" {
		sqlStr += " AND severity_rank >= ?"
		args = append(args, severityRank(q.MinSeverity))
	}
	sqlStr += " ORDER BY when_unix DESC, id DESC"
	if q.Limit > 0 {
		sqlStr += " LIMIT ?"
		args = append(args, q.Limit)
	}

	rows, err := s.db.QueryContext(ctx, sqlStr, args...)
	if err != nil {
		return nil, fmt.Errorf("sqlite record store query: %w", err)
	}
	defer rows.Close()

	out := make([]Record, 0)
	for rows.Next() {
		var (
			whenUnix int64
			r        Record
		)
		if err := rows.Scan(&whenUnix, &r.HostID, &r.HostName, &r.AgentRoot,
			&r.Event, &r.Severity, &r.RuleID, &r.Technique, &r.RulePack, &r.RuleVersion,
			&r.ActionType, &r.ActionSource, &r.Path, &r.Summary); err != nil {
			return nil, fmt.Errorf("sqlite record store scan: %w", err)
		}
		r.When = unixToTime(whenUnix)
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("sqlite record store iterate: %w", err)
	}
	return out, nil
}

// Recent implements RecordStore, defined in terms of Query.
func (s *SQLiteRecordStore) Recent(ctx context.Context, hostID string, limit int) ([]Record, error) {
	return s.Query(ctx, RecordQuery{HostID: hostID, Limit: limit})
}

// Close releases the database handle.
func (s *SQLiteRecordStore) Close() error { return s.db.Close() }

// init wires the SQLite backend into the config's RecordStoreFn hook, so
// `record_backend: sqlite` selects it in this build. In a build without
// `-tags sqlite` the hook stays nil and the config returns a clear error if
// someone asks for sqlite anyway.
func init() {
	sqliteRecordStoreFn = func(path string, retain RecordRetention) (RecordStore, error) {
		return NewSQLiteRecordStore(path, retain)
	}
}

// nowUnix is the current time in unix seconds (UTC). A method so a test can
// substitute a clock if needed; today it is just time.Now.
func (s *SQLiteRecordStore) nowUnix() int64 { return time.Now().UTC().Unix() }
