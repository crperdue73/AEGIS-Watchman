package buildinfo

import "testing"

func TestStringDefaults(t *testing.T) {
	// The package defaults: a bare `go build` with no ldflags.
	if got := String("watchman-central"); got != "watchman-central dev" {
		t.Errorf("default String = %q, want %q", got, "watchman-central dev")
	}
}

func TestStringStamped(t *testing.T) {
	// Save and restore so the mutation cannot leak to other tests.
	version, commit, date := Version, Commit, Date
	defer func() { Version, Commit, Date = version, commit, date }()

	Version, Commit, Date = "0.1.0", "a1b2c3d", "2026-09-29T20:00:00Z"
	want := "watchman-collector 0.1.0 (commit a1b2c3d, built 2026-09-29T20:00:00Z)"
	if got := String("watchman-collector"); got != want {
		t.Errorf("stamped String = %q, want %q", got, want)
	}
}
