package main

import (
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/CRPerdue/agentids/internal/rules"
)

// runPin writes a pin file for a pack set to stdout: the byte-exact record of
// which packs (and which revisions) a deployment approves. Redirect it into the
// repo next to the packs:
//
//	agentids-rules pin rules/ > rules/packs.pins
func runPin(args []string) {
	if isHelp(args) {
		fmt.Fprintln(os.Stderr, "usage: agentids-rules pin [pack-file-or-dir ...]   (default: ./rules)")
		return
	}
	paths := args
	if len(paths) == 0 {
		paths = []string{defaultPackPath}
	}
	pins, err := rules.PinSet(paths)
	if err != nil {
		fmt.Fprintf(os.Stderr, "agentids-rules: %v\n", err)
		os.Exit(1)
	}
	if len(pins) == 0 {
		fmt.Fprintf(os.Stderr, "agentids-rules: no packs found in %s\n", strings.Join(paths, ", "))
		os.Exit(1)
	}
	os.Stdout.Write(rules.FormatPins(pins))
}

// checkPins verifies the loaded pack set against a pin file and returns a
// non-nil error if they disagree. It is the acceptance test for "the fleet runs
// the packs we approved": a changed, missing, or new pack is a hard failure.
func checkPins(out io.Writer, pinsFile string, paths []string) error {
	data, err := os.ReadFile(pinsFile)
	if err != nil {
		return fmt.Errorf("read pin file: %w", err)
	}
	pinned, err := rules.ParsePins(data)
	if err != nil {
		return err
	}
	actual, err := rules.PinSet(paths)
	if err != nil {
		return err
	}
	issues := rules.DiffPins(pinned, actual)
	if len(issues) == 0 {
		fmt.Fprintf(out, "pins: ok — %d pack(s) match %s\n", len(actual), pinsFile)
		return nil
	}
	fmt.Fprintf(out, "pins: %d drift(s) against %s\n", len(issues), pinsFile)
	for _, is := range issues {
		switch is.Kind {
		case rules.PinChanged:
			fmt.Fprintf(out, "   changed  %s: pinned %s, found %s\n", is.File, short(is.Want), short(is.Got))
		case rules.PinMissing:
			fmt.Fprintf(out, "   missing  %s (pinned, not present)\n", is.File)
		case rules.PinUnpinned:
			fmt.Fprintf(out, "   unpinned %s (%s): not in the pin file\n", is.File, short(is.Got))
		default:
			fmt.Fprintf(out, "   %s  %s\n", is.Kind, is.File)
		}
	}
	return fmt.Errorf("pack set does not match %s", pinsFile)
}

// short abbreviates a hex digest for a one-line report.
func short(sha string) string {
	if len(sha) > 12 {
		return sha[:12]
	}
	return sha
}

// splitPinsFlag was folded into parseVerifyArgs (see signpins.go), which also
// understands --pubkey/--sig. Removed to keep one parser for `verify`.
