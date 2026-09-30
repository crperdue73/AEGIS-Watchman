package central

import (
	"path/filepath"
	"testing"
)

// TestLocalCentralExampleConfigLoads guards the config the quickstart tells a
// stranger to run (examples/central.local.yaml): it must load, and it must stay
// a *local* config — no real alert sinks that would page someone from a
// laptop. A local example that quietly gained a webhook or an SMTP host would
// be a footgun, so the guard fails on it.
func TestLocalCentralExampleConfigLoads(t *testing.T) {
	cfg, err := LoadConfig(filepath.Join("..", "..", "examples", "central.local.yaml"))
	if err != nil {
		t.Fatalf("examples/central.local.yaml does not load: %v", err)
	}
	if cfg.Addr == "" || cfg.Store == "" {
		t.Errorf("local central config missing addr/store: %+v", cfg)
	}
	if cfg.Alerts.Webhook != "" || cfg.Alerts.Email.Host != "" {
		t.Errorf("local central config must not configure real alert sinks: %+v", cfg.Alerts)
	}
	if cfg.UidMap != "" || cfg.Manifests.Dir != "" {
		t.Errorf("local central config must not point at system paths: uid_map=%q manifests=%q", cfg.UidMap, cfg.Manifests.Dir)
	}
}
