package central

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

// Config is the central service's on-disk configuration (central.yaml). It is
// intentionally small in Phase 1: where the baselines live, how much diff to
// carry, and where alerts go.
type Config struct {
	// Addr is the listen address, e.g. ":8080".
	Addr string `yaml:"addr"`
	// Store is the path to the durable baseline store file.
	Store string `yaml:"store"`
	// MaxDiffBytes caps the unified diff attached to each drift event.
	MaxDiffBytes int `yaml:"max_diff_bytes"`

	Alerts Alerts `yaml:"alerts"`
}

// Alerts configures notification delivery for drift events.
type Alerts struct {
	// Webhook is an HTTP endpoint that receives a JSON Alert. Empty disables it.
	Webhook string `yaml:"webhook"`
	// Log writes a one-line summary per alert to the service log.
	Log bool `yaml:"log"`
}

// DefaultConfig returns the Phase-1 defaults, matching the built-in flag
// defaults so a missing config file and an empty config file behave the same.
func DefaultConfig() Config {
	return Config{
		Addr:         ":8080",
		Store:        "agentids-baselines.json",
		MaxDiffBytes: 4096,
		Alerts:       Alerts{Log: true},
	}
}

// LoadConfig reads central.yaml from path. A missing file returns the defaults
// with no error, so `agentids-central` runs out of the box.
func LoadConfig(path string) (Config, error) {
	cfg := DefaultConfig()

	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return cfg, nil
		}
		return cfg, fmt.Errorf("read central config: %w", err)
	}
	if err := yaml.Unmarshal(raw, &cfg); err != nil {
		return cfg, fmt.Errorf("parse central config: %w", err)
	}
	if err := cfg.validate(); err != nil {
		return cfg, err
	}
	return cfg, nil
}

func (c Config) validate() error {
	if c.Addr == "" {
		return fmt.Errorf("config: addr is empty")
	}
	if c.Store == "" {
		return fmt.Errorf("config: store is empty")
	}
	if c.MaxDiffBytes < 0 {
		return fmt.Errorf("config: max_diff_bytes must be >= 0")
	}
	return nil
}

// Alerter builds the Alerter described by the config, or nil when no sink is
// enabled. Webhook and log sinks compose via MultiAlerter.
func (c Config) Alerter(logger Logger) Alerter {
	var sinks []Alerter
	if c.Alerts.Webhook != "" {
		sinks = append(sinks, NewWebhookAlerter(c.Alerts.Webhook))
	}
	if c.Alerts.Log && logger != nil {
		sinks = append(sinks, &LogAlerter{Logger: logger})
	}
	switch len(sinks) {
	case 0:
		return nil
	case 1:
		return sinks[0]
	default:
		return &MultiAlerter{Alerters: sinks}
	}
}
