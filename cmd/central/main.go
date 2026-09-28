// Command agentids-central is the central rule/alert service.
//
// Phase 1 scope: accept agent inventories over HTTP, store per-agent baselines,
// compute drift, return identity.drift events, and alert on drift via a
// configured webhook and/or the service log. See central.yaml.example.
package main

import (
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/CRPerdue/agentids/internal/central"
)

func main() {
	configPath := flag.String("config", "central.yaml", "path to central config (missing file = defaults)")
	addr := flag.String("addr", "", "listen address (overrides config)")
	storePath := flag.String("store", "", "path to the baseline store file (overrides config)")
	webhook := flag.String("webhook", "", "webhook URL for drift alerts (overrides config)")
	showVersion := flag.Bool("version", false, "print version and exit")
	flag.Parse()

	if *showVersion {
		fmt.Println("agentids-central (phase 1)")
		return
	}

	cfg, err := central.LoadConfig(*configPath)
	if err != nil {
		log.Fatalf("agentids-central: %v", err)
	}
	// Flags win when explicitly set, so an operator can override without
	// editing the file.
	if *addr != "" {
		cfg.Addr = *addr
	}
	if *storePath != "" {
		cfg.Store = *storePath
	}
	if *webhook != "" {
		cfg.Alerts.Webhook = *webhook
	}

	store, err := central.NewFileStore(cfg.Store)
	if err != nil {
		log.Fatalf("agentids-central: %v", err)
	}

	svc := central.NewService(store)
	svc.MaxDiffBytes = cfg.MaxDiffBytes
	svc.SetLogger(log.Default())
	if a := cfg.Alerter(log.Default()); a != nil {
		svc.SetAlerter(a)
		if cfg.Alerts.Webhook != "" {
			log.Printf("agentids-central: webhook alerts -> %s", cfg.Alerts.Webhook)
		}
	} else {
		log.Printf("agentids-central: alerting disabled (no webhook, log off)")
	}

	srv := &http.Server{
		Addr:              cfg.Addr,
		Handler:           svc.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}

	log.Printf("agentids-central listening on %s (store=%s)", cfg.Addr, cfg.Store)
	if err := srv.ListenAndServe(); err != nil {
		log.Fatalf("agentids-central: %v", err)
	}
	os.Exit(0)
}
