package buildinfo

import "testing"

func TestStringDefaults(t *testing.T) {
	// The package defaults: a bare `go build` with no ldflags.
	if got := String("agentids-central"); got != "agentids-central dev" {
		t.Errorf("default String = %q, want %q", got, "agentids-central dev")
	}
}

func TestStringStamped(t *testing.T) {
	// Save and restore so the mutation cannot leak to other tests.
	version, commit, date := Version, Commit, Date
	defer func() { Version, Commit, Date = version, commit, date }()

	Version, Commit, Date = "0.1.0", "a1b2c3d", "2026-09-29T20:00:00Z"
	want := "agentids-collector 0.1.0 (commit a1b2c3d, built 2026-09-29T20:00:00Z)"
	if got := String("agentids-collector"); got != want {
		t.Errorf("stamped String = %q, want %q", got, want)
	}
}
