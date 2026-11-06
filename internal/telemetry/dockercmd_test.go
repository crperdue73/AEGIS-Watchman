package telemetry

import (
	"os/exec"
	"strings"
	"testing"
)

// recordCmds swaps the command seam for the duration of a test and returns a
// pointer to the captured argv lists plus a restore func.
func recordCmds(t *testing.T) (*[][]string, func()) {
	t.Helper()
	var got [][]string
	prev := newDockerCmd
	newDockerCmd = func(name string, args ...string) *exec.Cmd {
		got = append(got, append([]string{name}, args...))
		// A real *exec.Cmd is required by the type, but it must never run.
		return exec.Command("false")
	}
	return &got, func() { newDockerCmd = prev }
}

// BEAT 794. NewDockerSource was declared zero-coverage with
// next=inject-a-command-runner. What it actually decides is an argv, and before
// this test nothing in the tree could assert that argv -- a change that dropped
// --format or renamed the subcommand would have been silent.
func TestNewDockerSourceBuildsTheEventsArgv(t *testing.T) {
	got, restore := recordCmds(t)
	defer restore()

	src, err := NewDockerSource(DockerConfig{Enabled: true})
	if err != nil {
		t.Fatalf("NewDockerSource: %v", err)
	}
	if src == nil {
		t.Fatal("enabled config produced a nil source")
	}
	if len(*got) != 1 {
		t.Fatalf("built %d commands, want 1: %v", len(*got), *got)
	}
	want := []string{"docker", "events", "--format", "{{json .}}"}
	if strings.Join((*got)[0], "\x00") != strings.Join(want, "\x00") {
		t.Errorf("argv = %q, want %q", (*got)[0], want)
	}
}

// The config's own Bin/EventsArgs must reach the process, not be overwritten by
// the defaults. A test of the default path alone would pass even if every
// override were ignored.
func TestNewDockerSourceHonoursConfiguredBinAndArgs(t *testing.T) {
	got, restore := recordCmds(t)
	defer restore()

	_, err := NewDockerSource(DockerConfig{
		Enabled:    true,
		Bin:        "/usr/local/bin/podman",
		EventsArgs: []string{"events", "--filter", "type=container"},
	})
	if err != nil {
		t.Fatalf("NewDockerSource: %v", err)
	}
	want := []string{"/usr/local/bin/podman", "events", "--filter", "type=container"}
	if len(*got) != 1 || strings.Join((*got)[0], "\x00") != strings.Join(want, "\x00") {
		t.Errorf("argv = %v, want %q", *got, want)
	}
}

// Enabled=false must build nothing at all -- the collector constructs this
// unconditionally and relies on nil meaning "not on".
func TestNewDockerSourceDisabledBuildsNothing(t *testing.T) {
	got, restore := recordCmds(t)
	defer restore()

	src, err := NewDockerSource(DockerConfig{Enabled: false})
	if err != nil {
		t.Fatalf("disabled config errored: %v", err)
	}
	if src != nil {
		t.Errorf("disabled config produced a source; it must be nil so an unpolled source costs nothing")
	}
	if len(*got) != 0 {
		t.Errorf("disabled config ran %v; it must spawn nothing", *got)
	}
}

// Inspect's argv is this package's decision too. The daemon is not present in
// CI, so the command runs `false` and errors -- that is fine and expected: the
// assertion is on the argv, and the error path is asserted to be reported rather
// than swallowed.
func TestCLIDockerInspectorBuildsTheInspectArgv(t *testing.T) {
	got, restore := recordCmds(t)
	defer restore()

	_, err := CLIDockerInspector{}.Inspect("abc123")
	if len(*got) != 1 {
		t.Fatalf("built %d commands, want 1", len(*got))
	}
	want := []string{"docker", "inspect", "--format", "{{json .HostConfig}}", "abc123"}
	if strings.Join((*got)[0], "\x00") != strings.Join(want, "\x00") {
		t.Errorf("argv = %q, want %q", (*got)[0], want)
	}
	if err == nil {
		t.Errorf("a failing inspect must return an error so the caller can emit an unenriched event; "+
			"got nil for argv %v", (*got)[0])
	}
}
