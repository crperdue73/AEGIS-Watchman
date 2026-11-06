package collector

// Beat 797: `(*Spool).rewrite` is the mechanism that keeps a crash from shredding the spool. It was
// named by NO Go test -- `unmirrored.py` found it once the guard was widened past internal/rules,
// while four Python scripts (appendcost, spoolackcensus, spoolwedge, trimfloor) each transcribed a
// few lines of it in prose and asserted their own transcription. A mirror agreeing with itself is
// not evidence about the engine (finding 2026-11-03e), so these tests are the real instrument.
//
// What is deliberately NOT claimed: nothing here simulates a crash mid-write. That needs a
// fault-injecting filesystem and would be its own P2 item. These assert the properties the
// doc comment promises that CAN be observed from outside: the old content is replaced wholesale,
// a stale temp file does not corrupt the result, and the empty case removes rather than truncates.

import (
	"os"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

func recs(n int) []spoolRecord {
	out := make([]spoolRecord, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, spoolRecord{Host: schema.Host{ID: "h1"}})
	}
	return out
}

// The whole point: after a rewrite the file holds EXACTLY the new records. If rewrite appended
// instead of replacing, evidence would be duplicated on every drain -- invisible in a count, fatal
// in a ledger.
func TestSpoolRewriteReplacesRatherThanAppends(t *testing.T) {
	s := newTestSpool(t)
	if err := s.rewrite(recs(3)); err != nil {
		t.Fatalf("first rewrite: %v", err)
	}
	if err := s.rewrite(recs(1)); err != nil {
		t.Fatalf("second rewrite: %v", err)
	}

	got, err := s.read()
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("after rewriting 3 records then 1, spool holds %d records; rewrite must REPLACE "+
			"the file, not append to it", len(got))
	}
}

// An empty batch must remove the file. Leaving a zero-length file behind is not a crash risk, but
// `os.Stat` on it makes an empty spool indistinguishable from a spool that failed to clean up --
// and the trim/drain loops branch on existence.
func TestSpoolRewriteEmptyBatchRemovesTheFile(t *testing.T) {
	s := newTestSpool(t)
	if err := s.rewrite(recs(2)); err != nil {
		t.Fatalf("seed: %v", err)
	}
	if err := s.rewrite(nil); err != nil {
		t.Fatalf("rewrite(nil): %v", err)
	}

	if _, err := os.Stat(s.path); !os.IsNotExist(err) {
		t.Fatalf("rewrite(nil) left the spool file in place (stat err = %v); an empty batch must "+
			"remove it, not truncate it", err)
	}
}

// A leftover ".tmp" from a previous crash must not leak into the new file. rewrite opens with
// O_TRUNC, so the assertion is that the result holds the new records and NOT a concatenation with
// whatever a previous run left behind.
func TestSpoolRewriteIgnoresAStaleTempFile(t *testing.T) {
	s := newTestSpool(t)
	if err := os.WriteFile(s.path+".tmp", []byte("{\"Path\":\"/tmp/stale\"}\n"), 0o640); err != nil {
		t.Fatalf("seed stale temp: %v", err)
	}

	if err := s.rewrite(recs(1)); err != nil {
		t.Fatalf("rewrite: %v", err)
	}
	got, err := s.read()
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("spool holds %d records after one rewrite over a stale temp file; the temp file "+
			"was not truncated and its contents merged into the spool", len(got))
	}
}

// rewrite is the crash-safe path, so it must leave NO temp file behind on success. A surviving
// .tmp is how a half-written spool gets picked up by the next run.
func TestSpoolRewriteLeavesNoTempFileOnSuccess(t *testing.T) {
	s := newTestSpool(t)
	if err := s.rewrite(recs(2)); err != nil {
		t.Fatalf("rewrite: %v", err)
	}
	if _, err := os.Stat(s.path + ".tmp"); !os.IsNotExist(err) {
		t.Fatalf("rewrite left %s.tmp behind after a successful rename (stat err = %v)", s.path, err)
	}
}
