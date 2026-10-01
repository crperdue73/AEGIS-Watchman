// Command watchman-central is the central rule/alert service.
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

	"github.com/crperdue73/AEGIS-Watchman/internal/buildinfo"
	"github.com/crperdue73/AEGIS-Watchman/internal/central"
)

func main() {
	configPath := flag.String("config", "central.yaml", "path to central config (missing file = defaults)")
	addr := flag.String("addr", "", "listen address (overrides config)")
	storePath := flag.String("store", "", "path to the baseline store file (overrides config)")
	webhook := flag.String("webhook", "", "webhook URL for drift alerts (overrides config)")
	showVersion := flag.Bool("version", false, "print version and exit")
	flag.Parse()

	if *showVersion {
		fmt.Println(buildinfo.String("watchman-central"))
		return
	}

	cfg, err := central.LoadConfig(*configPath)
	if err != nil {
		log.Fatalf("watchman-central: %v", err)
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
		log.Fatalf("watchman-central: %v", err)
	}

	svc := central.NewService(store)
	svc.MaxDiffBytes = cfg.MaxDiffBytes
	svc.SetLogger(log.Default())
	// The event-record store backs the fleet view (GET /api/v1/fleet). Durable
	// when records.path is set; in-memory (resets on restart) otherwise. Never
	// nil, so the fleet endpoint is always functional.
	if rs, err := cfg.RecordStoreFn(); err != nil {
		log.Fatalf("watchman-central: %v", err)
	} else {
		svc.SetRecordStore(rs)
		if cfg.Records != "" {
			retain := cfg.ResolvedRecordRetention()
			log.Printf("watchman-central: fleet view records -> %s (backend=%s, retain=%dd, max=%d)", cfg.Records, cfg.RecordBackendOrDefault(), int(retain.Window.Hours()/24), retain.MaxRecords)
		} else {
			log.Printf("watchman-central: fleet view records in-memory only (set records to persist)")
		}
	}
	if reg, err := cfg.ManifestRegistry(); err != nil {
		log.Fatalf("watchman-central: %v", err)
	} else if reg != nil {
		svc.SetManifests(reg)
		log.Printf("watchman-central: layer 2 provenance enabled (manifest dir=%s, require_approval=%v)",
			cfg.Manifests.Dir, cfg.Manifests.RequireApproval)
	} else {
		log.Printf("watchman-central: layer 2 provenance disabled (no manifests.dir)")
	}
	if uids, err := cfg.UidResolverFn(); err != nil {
		log.Fatalf("watchman-central: %v", err)
	} else {
		svc.SetUidResolver(uids)
		if uids.Enabled() {
			log.Printf("watchman-central: uid->name resolution enabled (map=%s)", cfg.UidMap)
		}
	}
	if a := cfg.Alerter(log.Default()); a != nil {
		svc.SetAlerter(a)
		if cfg.Alerts.Webhook != "" {
			log.Printf("watchman-central: webhook alerts -> %s", cfg.Alerts.Webhook)
		}
	} else {
		log.Printf("watchman-central: alerting disabled (no webhook, log off)")
	}

	srv := &http.Server{
		Addr:              cfg.Addr,
		Handler:           svc.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}

	log.Printf("watchman-central listening on %s (store=%s)", cfg.Addr, cfg.Store)

	if cfg.TLSEnabled() {
		tlsCfg, err := cfg.TLS.ServerConfig()
		if err != nil {
			log.Fatalf("watchman-central: tls: %v", err)
		}
		srv.TLSConfig = tlsCfg
		log.Printf("watchman-central: mutual TLS required on the ingest listener")
		if err := srv.ListenAndServeTLS("", ""); err != nil {
			log.Fatalf("watchman-central: %v", err)
		}
		os.Exit(0)
	}

	log.Printf("watchman-central: WARNING plaintext ingest (configure tls for a real fleet)")
	if err := srv.ListenAndServe(); err != nil {
		log.Fatalf("watchman-central: %v", err)
	}
	os.Exit(0)
}
