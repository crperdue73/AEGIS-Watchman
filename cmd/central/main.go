// Command agentids-central is the central rule/alert service.
//
// Phase 1 scope: accept agent inventories over HTTP, store per-agent baselines,
// compute drift, and return identity.drift events. Alerting (webhook/email)
// hangs off the returned events.
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
	addr := flag.String("addr", ":8080", "listen address")
	storePath := flag.String("store", "agentids-baselines.json", "path to the baseline store file (durable across restarts)")
	showVersion := flag.Bool("version", false, "print version and exit")
	flag.Parse()

	if *showVersion {
		fmt.Println("agentids-central (phase 1)")
		return
	}

	store, err := central.NewFileStore(*storePath)
	if err != nil {
		log.Fatalf("agentids-central: %v", err)
	}
	svc := central.NewService(store)

	srv := &http.Server{
		Addr:              *addr,
		Handler:           svc.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}

	log.Printf("agentids-central listening on %s", *addr)
	if err := srv.ListenAndServe(); err != nil {
		log.Fatalf("agentids-central: %v", err)
	}
	os.Exit(0)
}
