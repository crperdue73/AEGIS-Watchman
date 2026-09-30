// Command agentids-rules is the Layer 3 rule tooling CLI: it parses, validates,
// and inspects rule packs without a running collector, so a rule author can
// check a pack the way they check code.
//
//	# validate the in-repo pack (default path: ./rules)
//	agentids-rules verify
//
//	# validate one file, or several packs/dirs as one set
//	agentids-rules verify rules/carbonato.yaml
//	agentids-rules verify rules/ community-rules/
//
//	# see what a pack set defines
//	agentids-rules list rules/
package main

import (
	"fmt"
	"os"

	"github.com/CRPerdue/agentids/internal/buildinfo"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	switch os.Args[1] {
	case "verify":
		verify(os.Args[2:])
	case "list":
		list(os.Args[2:])
	case "pin":
		runPin(os.Args[2:])
	case "version", "-version", "--version":
		fmt.Println(buildinfo.String("agentids-rules"))
	case "-h", "--help", "help":
		usage()
	default:
		fmt.Fprintf(os.Stderr, "unknown command %q\n\n", os.Args[1])
		usage()
		os.Exit(2)
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `agentids-rules — Layer 3 rule tooling

commands:
  verify [--pins <file>] [path...]  parse, validate, and (optionally) check a pack set
  list   [path...]                  list the rules a pack set defines, sorted by id
  pin    [path...]                  write a pin file for a pack set (to stdout)
  version                           print version and build info

A path may be a pack file (*.yaml/*.yml) or a directory of packs. Rule ids must
be unique across everything given, not just within one pack.

A pin file is a byte-exact record of an approved pack set (name@version and
sha256 per pack). Regenerate one with 'pin'; check a set against one with
'verify --pins'. The pin describes the same files a collector would load.
`)
}

func verify(args []string) {
	if isHelp(args) {
		fmt.Fprintln(os.Stderr, "usage: agentids-rules verify [--pins <file>] [pack-file-or-dir ...]")
		return
	}
	pinsFile, paths, err := splitPinsFlag(args)
	if err != nil {
		fmt.Fprintf(os.Stderr, "agentids-rules: %v\n", err)
		os.Exit(2)
	}
	packs, err := loadSet(paths)
	if err != nil {
		fmt.Fprintf(os.Stderr, "agentids-rules: %v\n", err)
		os.Exit(1)
	}
	summarize(os.Stdout, packs)

	if pinsFile != "" {
		resolved := paths
		if len(resolved) == 0 {
			resolved = []string{defaultPackPath}
		}
		if err := checkPins(os.Stdout, pinsFile, resolved); err != nil {
			fmt.Fprintf(os.Stderr, "agentids-rules: %v\n", err)
			os.Exit(1)
		}
	}
}

func list(args []string) {
	if isHelp(args) {
		fmt.Fprintln(os.Stderr, "usage: agentids-rules list [pack-file-or-dir ...]")
		return
	}
	packs, err := loadSet(args)
	if err != nil {
		fmt.Fprintf(os.Stderr, "agentids-rules: %v\n", err)
		os.Exit(1)
	}
	listRules(os.Stdout, packs)
}

func isHelp(args []string) bool {
	return len(args) == 1 && (args[0] == "-h" || args[0] == "--help")
}
