// Command agentids-collector is the per-host collector.
//
// Phase 1: inventory + hash agent identity/persona/skill/config files and
// submit them to the central service. `-dry-run` prints the inventory and exits
// without contacting anything. See PHASE0-DISCOVERY.md.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/CRPerdue/agentids/internal/collector"
	"github.com/CRPerdue/agentids/internal/schema"
)

func main() {
	configPath := flag.String("config", "collector.yaml", "path to collector config")
	hostID := flag.String("host-id", "", "stable host id (defaults to hostname)")
	hostName := flag.String("host-name", "", "human host name (defaults to hostname)")
	dryRun := flag.Bool("dry-run", false, "print the inventory to stdout and exit")
	once := flag.Bool("once", false, "run one collection cycle and exit")
	showVersion := flag.Bool("version", false, "print version and exit")
	flag.Parse()

	if *showVersion {
		fmt.Println("agentids-collector (phase 1)")
		return
	}

	cfg, err := collector.LoadConfig(*configPath)
	if err != nil {
		log.Fatalf("agentids-collector: %v", err)
	}

	collected, err := collector.CollectAll(cfg)
	if err != nil {
		log.Fatalf("agentids-collector: %v", err)
	}

	host := resolveHost(*hostID, *hostName)

	if *dryRun {
		out := struct {
			Host   schema.Host           `json:"host"`
			Agents []collector.Collected `json:"agents"`
		}{Host: host, Agents: collected}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(out); err != nil {
			log.Fatalf("agentids-collector: %v", err)
		}
		return
	}

	if cfg.Report.Endpoint == "" {
		log.Fatalf("agentids-collector: report.endpoint is required (or use -dry-run)")
	}
	var client *collector.Client
	if cfg.Report.TLS.Enabled() {
		c, err := collector.NewTLSClient(cfg.Report.Endpoint, cfg.Report.TLS)
		if err != nil {
			log.Fatalf("agentids-collector: tls: %v", err)
		}
		client = c
		log.Printf("agentids-collector: mutual TLS enabled -> %s", cfg.Report.Endpoint)
	} else {
		client = collector.NewClient(cfg.Report.Endpoint)
		log.Printf("agentids-collector: WARNING plaintext report -> %s", cfg.Report.Endpoint)
	}

	// Layer 3: action rules. One engine, bound to the single agent root when
	// there is exactly one (the common case) so a match is attributed to the
	// agent it concerns. All rules share the host-wide audit tail. Multi-agent
	// attribution needs a per-agent identity file and is deferred.
	var behavior *collector.BehaviorEngine
	if cfg.Behavior.RulesDir != "" {
		var l3agent *schema.Agent
		if len(collected) == 1 {
			l3agent = &schema.Agent{ID: filepath.Base(collected[0].Root), Root: collected[0].Root}
		}
		behavior, err = collector.NewBehaviorEngine(cfg, host, l3agent, client)
		if err != nil {
			log.Fatalf("agentids-collector: behavior: %v", err)
		}
		log.Printf("agentids-collector: Layer 3 enabled (rules=%s, audit_log=%q)",
			cfg.Behavior.RulesDir, cfg.Behavior.AuditLog)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	cycle := func() {
		c, cancel := context.WithTimeout(ctx, 60*time.Second)
		defer cancel()
		for _, col := range collected {
			agent := schema.Agent{ID: filepath.Base(col.Root), Root: col.Root}
			res, err := client.Submit(c, collector.InventoryRequest{
				Host: host, Agent: agent, Inv: col.Inventory,
			})
			if err != nil {
				log.Printf("collector: submit %s: %v", col.Root, err)
				continue
			}
			if res.FirstSeen {
				log.Printf("collector: enrolled %s (%d files)", col.Root, len(col.Inventory.Files))
				continue
			}
			for _, ev := range res.Events {
				log.Printf("collector: DRIFT %s %s (severity=%s)", ev.File.Path, ev.Event, ev.Severity)
			}
		}
		// Layer 3: read new host activity and evaluate rules. Runs after the
		// file cycle so a persona edit and the action it triggers are seen in
		// the same tick.
		behavior.Poll()
	}

	cycle()
	if *once {
		return
	}

	log.Printf("agentids-collector: polling every %s", cfg.Report.Interval)
	ticker := time.NewTicker(cfg.Report.Interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			log.Printf("agentids-collector: shutting down")
			return
		case <-ticker.C:
			collected, err = collector.CollectAll(cfg)
			if err != nil {
				log.Printf("collector: re-walk: %v", err)
				continue
			}
			cycle()
		}
	}
}

func resolveHost(idOverride, nameOverride string) schema.Host {
	hn, _ := os.Hostname()
	id := idOverride
	if id == "" {
		id = hn
	}
	name := nameOverride
	if name == "" {
		name = hn
	}
	return schema.Host{ID: id, Name: name}
}
