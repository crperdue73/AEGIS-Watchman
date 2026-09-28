// Command agentids-central is the central rule/alert service.
//
// Phase 1 scope: accept identity.drift events over mTLS, store per-agent
// baselines, compute drift, and fire webhook/email alerts.
//
// This is a scaffold — the HTTP surface and store land in Phase 1.
package main

import (
	"flag"
	"fmt"
	"os"
)

func main() {
	configPath := flag.String("config", "central.yaml", "path to central config")
	version := flag.Bool("version", false, "print version and exit")
	flag.Parse()

	if *version {
		fmt.Println("agentids-central (scaffold)")
		return
	}

	// TODO(phase1): config, baseline store, /api/v1/events ingest, drift, alerting.
	fmt.Fprintf(os.Stderr, "agentids-central: scaffold; config=%s\n", *configPath)
	os.Exit(0)
}
