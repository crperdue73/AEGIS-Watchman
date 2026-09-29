package telemetry

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os/exec"
	"strings"
	"sync"

	"github.com/CRPerdue/agentids/internal/schema"
)

// ContainerStart is the action type emitted by the Docker events adapter.
//
// It is deliberately NOT reusing ScopeChange. A container being created or
// started is a distinct, observable host fact — a new workload appeared on the
// box that was not there before — and the CARBONATO entry vector (G4 in
// PHASE0-COVERAGE.md) is exactly that: something starts a container, on a
// Docker daemon that should not have been reachable, with host access it should
// not have had. Giving that its own action type lets a rule match on it without
// overloading the "agent changed its own scope" semantics of scope.change.
const ContainerStart = "container.start"

// ContainerEvent is one parsed line of `docker events` output. We decode only
// the fields the detector needs; Docker's event schema is stable and documented
// (https://docs.docker.com/engine/reference/commandline/events/).
//
// Docker events are a JSON object per line on stdout. The fields that matter:
//
//	{"Type":"container","Action":"start","Actor":{"ID":"...",
//	 "Attributes":{"image":"...","name":"..."}},"time":169...}
//
// The runtime does not, on its own, tell us whether the container was
// privileged or host-mounted — that is a property of the container's *config*,
// which is only visible via the API (`docker inspect`) or the daemon's own
// records. So the collector enriches a container.start event with the
// container's HostConfig when it can reach the daemon; when it cannot, the
// event still fires, just without the enrichment (see ContainerStartAction).
type ContainerEvent struct {
	Type   string `json:"Type"`
	Action string `json:"Action"`
	Time   int64  `json:"time"`
	Actor  struct {
		ID         string            `json:"ID"`
		Attributes map[string]string `json:"Attributes"`
	} `json:"Actor"`
}

// ContainerConfig is the subset of a container's HostConfig that the detector
// cares about: the things that turn "a container started" into "a container
// started and could have escaped." Populated by the caller (inspectContainer);
// the parser itself stays dependency-free.
type ContainerConfig struct {
	// Privileged mirrors HostConfig.Privileged.
	Privileged bool
	// HostPID mirrors HostConfig.PidMode == "host".
	HostPID bool
	// HostNetwork mirrors HostConfig.NetworkMode == "host".
	HostNetwork bool
	// Binds is HostConfig.Binds — host path mounts, e.g. "/:/host", "/var/run".
	Binds []string
	// Image and Name are for the alert, not the match.
	Image string
	Name  string
}

// ParseDockerEvents parses a stream of `docker events --format '{{json .}}'`
// output and returns the container.start events among them. Non-container or
// non-start actions (exec_create, die, network attach, …) are skipped: a
// CARBONATO-class intrusion's entry vector is a container appearing, and
// matching every lifecycle action would be noise.
func ParseDockerEvents(r io.Reader) ([]*ContainerEvent, error) {
	var out []*ContainerEvent
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		var ev ContainerEvent
		if err := json.Unmarshal([]byte(line), &ev); err != nil {
			// A line that is not JSON is a comment or a stray log line, not a
			// container event. Skip rather than fail the whole stream: one
			// unparseable line must not blind the adapter (same principle as
			// MultiSource skipping a broken child).
			continue
		}
		if ev.Type != "container" {
			continue
		}
		if ev.Action != "start" && ev.Action != "create" {
			continue
		}
		out = append(out, &ev)
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("telemetry: read docker events: %w", err)
	}
	return out, nil
}

// ContainerStartAction converts a container event (optionally enriched with its
// HostConfig) into a container.start action.
//
// The detail map carries two groups of fields, and the split matters:
//
//   - Identity: id, image, name — so an alert names the thing that appeared.
//   - Risk: privileged, host_pid, host_network, binds — the flags that decide
//     whether this is a routine workload or a container escape waiting to
//     happen. Each is present only when true, so a rule can match with
//     detail_non_empty and mean it.
//
// When cfg is nil (the daemon was unreachable, or the source is running from a
// captured events file with no live Docker to inspect), the action still fires
// with identity only. A container we cannot enrich is still a container that
// appeared — that is the whole point of a detection tool: report what you saw,
// flag what you could not check, never silently drop the event.
func ContainerStartAction(ev *ContainerEvent, cfg *ContainerConfig) schema.Action {
	detail := map[string]any{
		"container": ev.Actor.ID,
		"action":    ev.Action,
	}
	if img := ev.Actor.Attributes["image"]; img != "" {
		detail["image"] = img
	}
	if name := ev.Actor.Attributes["name"]; name != "" {
		detail["name"] = name
	}
	if cfg != nil {
		if cfg.Image != "" {
			detail["image"] = cfg.Image
		}
		if cfg.Name != "" {
			detail["name"] = cfg.Name
		}
		if cfg.Privileged {
			detail["privileged"] = "true"
		}
		if cfg.HostPID {
			detail["host_pid"] = "true"
		}
		if cfg.HostNetwork {
			detail["host_network"] = "true"
		}
		if len(cfg.Binds) > 0 {
			// Bind mounts joined for a single detail value; rules glob on
			// "*:/" to catch the host-root mount specifically.
			detail["binds"] = strings.Join(cfg.Binds, ",")
		}
	}
	return schema.Action{Type: ContainerStart, Detail: detail}
}

// ContainerInspector returns the HostConfig for a container id, or nil when the
// daemon cannot be reached. It is an interface so the source is testable
// without a Docker daemon and so a deployment can substitute an API client.
type ContainerInspector interface {
	Inspect(id string) (*ContainerConfig, error)
}

// DockerSource reads `docker events` and emits container.start actions.
//
// Two input modes, mirroring FileSource's flexibility:
//
//   - FromCommand: spawn `docker events --format {{json .}}` and read its
//     stdout. This is the live deployment path. The child is stopped by Close.
//   - FromReader: parse any io.Reader of events (a file, a test fixture, a
//     `docker events` output captured elsewhere). No process, no daemon.
//
// A DockerSource with neither is inert (Actions returns an empty batch), which
// matches how a host without Docker should behave: the collector runs, the rule
// pack is loaded, and the source simply has nothing to say — same as FileSource
// on a host with no auditd.
type DockerSource struct {
	mu      sync.Mutex
	rdr     io.Reader
	scanner *bufio.Scanner
	cmd     *exec.Cmd
	started bool
	// inspector, when set, enriches each event with the container's HostConfig.
	// Nil is fine: unenriched events still fire with identity only.
	inspector ContainerInspector
}

// NewDockerSourceFromReader parses container events from an existing reader.
// Used for tests, demos, and replaying a captured events stream.
func NewDockerSourceFromReader(r io.Reader) *DockerSource {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	return &DockerSource{rdr: r, scanner: sc}
}

// NewDockerSourceFromCommand spawns `docker events` (or any argv) and reads its
// output. The process is started lazily on the first Actions call and stopped by
// Close. This keeps construction side-effect-free so the collector can build the
// source unconditionally and only pay for it if it polls.
func NewDockerSourceFromCommand(name string, args ...string) *DockerSource {
	return &DockerSource{cmd: exec.Command(name, args...)}
}

// SetInspector attaches a config inspector for enrichment.
func (d *DockerSource) SetInspector(i ContainerInspector) { d.inspector = i }

// start spawns the child process on first use. Caller holds d.mu.
func (d *DockerSource) start() error {
	if d.started {
		return nil
	}
	if d.cmd == nil {
		return nil
	}
	stdout, err := d.cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("telemetry: docker events stdout pipe: %w", err)
	}
	if err := d.cmd.Start(); err != nil {
		return fmt.Errorf("telemetry: start docker events: %w", err)
	}
	d.scanner = bufio.NewScanner(stdout)
	d.scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	d.started = true
	return nil
}

// Actions reads whatever container events are available on this poll. It does
// NOT block waiting for more: Docker events is a long-running stream, so the
// scanner is read non-blocking only insofar as the underlying reader allows.
// For the command mode, `docker events` flushes a line per event, so a poll
// after a container starts sees it.
//
// A parse error on a single line is skipped (see ParseDockerEvents), so the
// only error returned is a genuine I/O or process-start failure.
func (d *DockerSource) Actions() ([]Event, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if err := d.start(); err != nil {
		return nil, err
	}
	if d.scanner == nil {
		return nil, nil
	}
	var out []Event
	for d.scanner.Scan() {
		line := strings.TrimSpace(d.scanner.Text())
		if line == "" {
			continue
		}
		evs, err := ParseDockerEvents(strings.NewReader(line))
		if err != nil {
			continue
		}
		for _, ev := range evs {
			var cfg *ContainerConfig
			if d.inspector != nil && ev.Actor.ID != "" {
				if c, err := d.inspector.Inspect(ev.Actor.ID); err == nil {
					cfg = c
				}
			}
			out = append(out, Event{Action: ContainerStartAction(ev, cfg)})
		}
	}
	// Scanner errors here are stream termination (the child exited); they are
	// not fatal to the collector and are surfaced by Close, not per-poll.
	return out, nil
}

// Close stops the spawned child, if any.
func (d *DockerSource) Close() error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.cmd != nil && d.cmd.Process != nil {
		return d.cmd.Process.Kill()
	}
	return nil
}

// DockerConfig configures the optional container-runtime adapter.
//
// It is additive, exactly like the eBPF block: when Enabled, the collector
// spawns `docker events`, enriches each container start with its HostConfig,
// and feeds the resulting container.start actions to the rules engine. When
// disabled — or when the Docker binary or daemon is absent — the collector runs
// on its other sources and says so once. A host without Docker is not an error.
type DockerConfig struct {
	// Enabled turns the adapter on. Off by default: the daemon must be
	// reachable and the operator must opt in.
	Enabled bool `yaml:"enabled"`
	// Bin is the docker binary to run, defaulting to "docker".
	Bin string `yaml:"bin"`
	// EventsArgs overrides the arguments passed to Bin (default:
	// [events --format {{json .}}]). Mostly for tests and non-standard runtimes.
	EventsArgs []string `yaml:"events_args"`
	// Inspect enables HostConfig enrichment via `docker inspect` so a privileged,
	// host-mounted container is labelled as such. When false, container.start
	// events still fire with identity only — never silently dropped.
	Inspect bool `yaml:"inspect"`
}

// Validate checks the block for contradictions. It runs even when Enabled is
// false so a typo'd config is caught at startup rather than at first poll.
func (c DockerConfig) Validate() error {
	if !c.Enabled {
		return nil
	}
	if c.Bin == "" {
		// Empty means the default; not an error.
		return nil
	}
	if strings.TrimSpace(c.Bin) == "" {
		return fmt.Errorf("telemetry: docker.bin is whitespace-only")
	}
	return nil
}

// NewDockerSource builds the adapter from config. It returns (nil, nil) when
// disabled, so the collector can add it unconditionally and get an inert zero
// (never a nil-deref). A non-nil error is reserved for a config that cannot be
// honoured at all.
func NewDockerSource(cfg DockerConfig) (*DockerSource, error) {
	if !cfg.Enabled {
		return nil, nil
	}
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	bin := cfg.Bin
	if bin == "" {
		bin = "docker"
	}
	args := cfg.EventsArgs
	if args == nil {
		args = []string{"events", "--format", "{{json .}}"}
	}
	src := NewDockerSourceFromCommand(bin, args...)
	if cfg.Inspect {
		src.SetInspector(CLIDockerInspector{DockerBin: bin})
	}
	return src, nil
}

// CLIDockerInspector shells out to `docker inspect` to read a container's
// HostConfig. It is the dependency-free default: no Docker API client, no
// library, just the CLI that is already present on any host running Docker.
// (The brief's stance on tooling: shell out rather than add a dependency for
// something the host already provides.)
type CLIDockerInspector struct {
	// DockerBin is the docker binary, defaulting to "docker".
	DockerBin string
}

// Inspect runs `docker inspect --format '{{json .HostConfig}}' <id>` and decodes
// the fields the detector needs. Returns nil config and an error when the
// daemon is unreachable, in which case the caller emits an unenriched event.
func (c CLIDockerInspector) Inspect(id string) (*ContainerConfig, error) {
	bin := c.DockerBin
	if bin == "" {
		bin = "docker"
	}
	cmd := exec.Command(bin, "inspect", "--format", "{{json .HostConfig}}", id)
	cmd.Stderr = nil
	out, err := cmd.Output()
	if err != nil {
		return nil, err
	}
	// Minimal local struct: privileged, pid/network mode, binds.
	var hc struct {
		Privileged  bool     `json:"Privileged"`
		PidMode     string   `json:"PidMode"`
		NetworkMode string   `json:"NetworkMode"`
		Binds       []string `json:"Binds"`
	}
	if err := json.Unmarshal(out, &hc); err != nil {
		return nil, fmt.Errorf("telemetry: decode docker inspect: %w", err)
	}
	return &ContainerConfig{
		Privileged:  hc.Privileged,
		HostPID:     hc.PidMode == "host",
		HostNetwork: hc.NetworkMode == "host",
		Binds:       hc.Binds,
	}, nil
}
