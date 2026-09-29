package telemetry

import (
	"strings"
	"testing"
)

// A real `docker events --format {{json .}}` line for a container start.
const dockerStartLine = `{"status":"start","id":"a1b2c3","from":"alpine","Type":"container","Action":"start","Actor":{"ID":"a1b2c3d4e5f6","Attributes":{"image":"alpine:latest","name":"worker"}},"time":1696000000,"timeNano":1696000000000000000}`

func TestParseDockerEventsKeepsContainerStart(t *testing.T) {
	out, err := ParseDockerEvents(strings.NewReader(dockerStartLine))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(out) != 1 {
		t.Fatalf("got %d events, want 1", len(out))
	}
	if out[0].Actor.ID != "a1b2c3d4e5f6" {
		t.Errorf("id = %q", out[0].Actor.ID)
	}
}

func TestParseDockerEventsSkipsNonContainer(t *testing.T) {
	lines := strings.Join([]string{
		`{"Type":"network","Action":"connect","Actor":{"ID":"n1"}}`,
		`{"Type":"container","Action":"die","Actor":{"ID":"c1"}}`,
		`{"Type":"container","Action":"exec_create: /bin/sh","Actor":{"ID":"c1"}}`,
		dockerStartLine,
	}, "\n")
	out, err := ParseDockerEvents(strings.NewReader(lines))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(out) != 1 {
		t.Fatalf("got %d events, want 1 (only the container start)", len(out))
	}
}

func TestParseDockerEventsSkipsGarbageLine(t *testing.T) {
	// A non-JSON line (a comment or a stray log) must be skipped, never fail
	// the stream: one bad line must not blind the adapter.
	lines := "not json at all\n" + dockerStartLine
	out, err := ParseDockerEvents(strings.NewReader(lines))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(out) != 1 {
		t.Fatalf("got %d, want 1", len(out))
	}
}

func TestContainerStartActionIdentity(t *testing.T) {
	ev := &ContainerEvent{}
	ev.Type, ev.Action = "container", "start"
	ev.Actor.ID = "abc123"
	ev.Actor.Attributes = map[string]string{"image": "alpine", "name": "worker"}
	act := ContainerStartAction(ev, nil)
	if act.Type != ContainerStart {
		t.Fatalf("type = %q", act.Type)
	}
	if act.Detail["container"] != "abc123" || act.Detail["image"] != "alpine" || act.Detail["name"] != "worker" {
		t.Errorf("detail = %v", act.Detail)
	}
	// No enrichment -> no risk flags present at all.
	if _, ok := act.Detail["privileged"]; ok {
		t.Errorf("unenriched event must not claim privileged")
	}
}

func TestContainerStartActionEnrichedRisk(t *testing.T) {
	ev := &ContainerEvent{}
	ev.Type, ev.Action = "container", "start"
	ev.Actor.ID = "abc123"
	cfg := &ContainerConfig{
		Privileged:  true,
		HostPID:     true,
		HostNetwork: true,
		Binds:       []string{"/:/host", "/var/run/docker.sock:/var/run/docker.sock"},
		Image:       "alpine",
		Name:        "worker",
	}
	act := ContainerStartAction(ev, cfg)
	for _, k := range []string{"privileged", "host_pid", "host_network", "binds"} {
		if act.Detail[k] == "" {
			t.Errorf("detail[%q] missing on a privileged host-mounted container", k)
		}
	}
	if !strings.Contains(act.Detail["binds"].(string), "/:/host") {
		t.Errorf("binds = %v, want the host-root mount", act.Detail["binds"])
	}
}

// stubInspector returns a fixed config for any id.
type stubInspector struct{ cfg *ContainerConfig }

func (s stubInspector) Inspect(string) (*ContainerConfig, error) { return s.cfg, nil }

func TestDockerSourceReaderEnriches(t *testing.T) {
	src := NewDockerSourceFromReader(strings.NewReader(dockerStartLine))
	src.SetInspector(stubInspector{cfg: &ContainerConfig{Privileged: true, Binds: []string{"/:/host"}}})
	evs, err := src.Actions()
	if err != nil {
		t.Fatalf("actions: %v", err)
	}
	if len(evs) != 1 {
		t.Fatalf("got %d events", len(evs))
	}
	if evs[0].Action.Detail["privileged"] != "true" {
		t.Errorf("expected enrichment to mark privileged: %v", evs[0].Action.Detail)
	}
}

func TestDockerSourceReaderUnenrichedStillFires(t *testing.T) {
	src := NewDockerSourceFromReader(strings.NewReader(dockerStartLine))
	evs, err := src.Actions()
	if err != nil {
		t.Fatalf("actions: %v", err)
	}
	if len(evs) != 1 || evs[0].Action.Detail["container"] != "a1b2c3d4e5f6" {
		t.Fatalf("a container with no inspector must still fire with identity: %v", evs)
	}
}

func TestDockerSourceEmptyIsInert(t *testing.T) {
	// No reader, no command -> no events, no error (a host without Docker).
	src := &DockerSource{}
	evs, err := src.Actions()
	if err != nil || len(evs) != 0 {
		t.Fatalf("inert source = %v, %v", evs, err)
	}
	if err := src.Close(); err != nil {
		t.Fatalf("close inert source: %v", err)
	}
}
