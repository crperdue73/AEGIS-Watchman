package main

import (
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/crperdue73/AEGIS-Watchman/internal/provenance"
)

// verifyOpts are the flags `verify` accepts beyond its positional paths.
type verifyOpts struct {
	Pins  string   // --pins <file>: check the set against this pin file
	Pub   string   // --pubkey <file>: also verify the pin's detached signature
	Sig   string   // --sig <file>: signature path (default: <pins>.sig)
	Paths []string // positional pack files/dirs
}

// parseVerifyArgs pulls the verify flags out of args. It is the one parser for
// `verify`, so a flag with a missing value is an argument error rather than a
// path silently swallowed.
func parseVerifyArgs(args []string) (verifyOpts, error) {
	var o verifyOpts
	for i := 0; i < len(args); i++ {
		take := func(name string) (string, error) {
			if i+1 >= len(args) {
				return "", fmt.Errorf("%s needs a file argument", name)
			}
			i++
			return args[i], nil
		}
		a := args[i]
		var err error
		switch {
		case a == "--pins":
			o.Pins, err = take("--pins")
		case strings.HasPrefix(a, "--pins="):
			o.Pins = strings.TrimPrefix(a, "--pins=")
			if o.Pins == "" {
				err = fmt.Errorf("--pins needs a file argument")
			}
		case a == "--pubkey":
			o.Pub, err = take("--pubkey")
		case strings.HasPrefix(a, "--pubkey="):
			o.Pub = strings.TrimPrefix(a, "--pubkey=")
			if o.Pub == "" {
				err = fmt.Errorf("--pubkey needs a file argument")
			}
		case a == "--sig":
			o.Sig, err = take("--sig")
		case strings.HasPrefix(a, "--sig="):
			o.Sig = strings.TrimPrefix(a, "--sig=")
			if o.Sig == "" {
				err = fmt.Errorf("--sig needs a file argument")
			}
		default:
			o.Paths = append(o.Paths, a)
		}
		if err != nil {
			return o, err
		}
	}
	if o.Pub != "" && o.Pins == "" {
		return o, fmt.Errorf("--pubkey requires --pins")
	}
	if o.Sig != "" && o.Pub == "" {
		return o, fmt.Errorf("--sig requires --pubkey")
	}
	return o, nil
}

// checkPinSignature verifies the detached Ed25519 signature over a pin file.
// The signature covers the pin file's exact bytes, so it must be checked before
// the pin is trusted for anything. Default signature path: <pins>.sig.
func checkPinSignature(out io.Writer, pinsFile, pubFile, sigFile string) error {
	if sigFile == "" {
		sigFile = pinsFile + ".sig"
	}
	pinBytes, err := os.ReadFile(pinsFile)
	if err != nil {
		return fmt.Errorf("read pin file: %w", err)
	}
	pub, err := provenance.LoadPublicKey(pubFile)
	if err != nil {
		return err
	}
	sigRaw, err := os.ReadFile(sigFile)
	if err != nil {
		return fmt.Errorf("read pin signature %s (create it with `sign-pins`): %w", sigFile, err)
	}
	if err := provenance.VerifyDetached(pub, pinBytes, string(sigRaw)); err != nil {
		return err
	}
	fmt.Fprintf(out, "signature: ok — %s signed by %s\n", pinsFile, pubFile)
	return nil
}

// runSignPins writes a detached signature over a pin file, so a published pack
// set can carry proof of who approved it:
//
//	watchman-rules sign-pins --key ops.key rules/packs.pins   # -> rules/packs.pins.sig
//	watchman-rules verify --pins rules/packs.pins --pubkey ops.key.pub rules/
func runSignPins(args []string) {
	if isHelp(args) {
		fmt.Fprintln(os.Stderr, "usage: watchman-rules sign-pins --key <private-key> [--out <sig-file>] <pins-file>")
		return
	}
	fs := flag.NewFlagSet("sign-pins", flag.ExitOnError)
	keyPath := fs.String("key", "watchman.key", "Ed25519 private key file")
	outPath := fs.String("out", "", "signature output path (default: <pins-file>.sig)")
	_ = fs.Parse(args)
	if fs.NArg() != 1 {
		fmt.Fprintln(os.Stderr, "usage: watchman-rules sign-pins --key <private-key> [--out <sig-file>] <pins-file>")
		os.Exit(2)
	}
	pinsFile := fs.Arg(0)
	sigOut := *outPath
	if sigOut == "" {
		sigOut = pinsFile + ".sig"
	}

	data, err := os.ReadFile(pinsFile)
	if err != nil {
		fmt.Fprintf(os.Stderr, "watchman-rules: read pin file: %v\n", err)
		os.Exit(1)
	}
	priv, err := provenance.LoadPrivateKey(*keyPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "watchman-rules: %v\n", err)
		os.Exit(1)
	}
	sig, err := provenance.SignDetached(priv, data)
	if err != nil {
		fmt.Fprintf(os.Stderr, "watchman-rules: %v\n", err)
		os.Exit(1)
	}
	if err := os.WriteFile(sigOut, []byte(sig+"\n"), 0o644); err != nil {
		fmt.Fprintf(os.Stderr, "watchman-rules: write signature: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("signed %s -> %s\n", pinsFile, sigOut)
}
