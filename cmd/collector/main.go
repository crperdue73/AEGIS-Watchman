// Command agentids-collector is the per-host collector.
//
// Phase 1 scope: inventory + hash agent identity/persona/skill/config files,
// emit identity.drift events to the central service. See PHASE0-DISCOVERY.md.
//
// This is a scaffold — the flag surface and config loading land in Phase 1.
package main

import (
	"flag"
	"fmt"
	"os"
)

func main() {
	configPath := flag.String("config", "collector.yaml", "path to collector config")
	version := flag.Bool("version", false, "print version and exit")
	flag.Parse()

	if *version {
		fmt.Println("agentids-collector (scaffold)")
		return
	}

	// TODO(phase1): load config, walk agent_roots, hash + watch, ship events.
	fmt.Fprintf(os.Stderr, "agentids-collector: scaffold; config=%s\n", *configPath)
	os.Exit(0)
}
