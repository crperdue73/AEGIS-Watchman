package collector

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestShippedDemoPersonaIsClean guards a leak that shipped once. The demo's
// stand-in persona (demo/agent-root/SOUL.md) must be committed in its "before"
// state: run.sh appends an attacker edit between the two collector passes and
// restores the file with `git checkout`. If that edit is ever committed — a run
// interrupted before restore, then a `git add -A` — the public repo ships demo
// output *and* every run starts from an already-overwritten file, appending a
// second edit. The persona is a fixture, not output; this pins it.
func TestShippedDemoPersonaIsClean(t *testing.T) {
	path := filepath.Join("..", "..", "demo", "agent-root", "SOUL.md")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read demo persona: %v", err)
	}
	if strings.Contains(string(raw), "overwritten by attacker") {
		t.Errorf("demo/agent-root/SOUL.md is committed in its post-run state — " +
			"the demo will double-append the attacker edit; restore the pre-edit fixture")
	}
}
