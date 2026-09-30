// Command agentids-manifest is the operator tool for Layer 2 provenance: it
// builds (signs) and verifies approval manifests for an agent workspace.
//
// Approval workflow:
//
//	# once: create a signing key
//	agentids-manifest keygen -out ops.key
//
//	# approve the current state of a workspace
//	agentids-manifest sign -root /home/vivi/.openclaw/workspace-vivi \
//	    -key ops.key -agent vivi -out vivi.manifest.json
//
//	# check a workspace against an approved manifest
//	agentids-manifest verify -root /home/vivi/.openclaw/workspace-vivi \
//	    -manifest vivi.manifest.json -pub ops.pub
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"

	"github.com/CRPerdue/agentids/internal/buildinfo"
	"github.com/CRPerdue/agentids/internal/provenance"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	switch os.Args[1] {
	case "keygen":
		keygen(os.Args[2:])
	case "sign":
		signCmd(os.Args[2:])
	case "verify":
		verifyCmd(os.Args[2:])
	case "version", "-version", "--version":
		fmt.Println(buildinfo.String("agentids-manifest"))
	case "-h", "--help", "help":
		usage()
	default:
		fmt.Fprintf(os.Stderr, "unknown command %q\n\n", os.Args[1])
		usage()
		os.Exit(2)
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `agentids-manifest — Layer 2 provenance approval tool

commands:
  keygen  generate an Ed25519 signing key pair
  sign    build and sign an approval manifest for an agent workspace
  verify  check a workspace against an approved manifest
  version print version and build info

run "agentids-manifest <command> -h" for flags
`)
}

func keygen(args []string) {
	fs := flag.NewFlagSet("keygen", flag.ExitOnError)
	out := fs.String("out", "agentids.key", "path to write the private key (public key gets .pub)")
	_ = fs.Parse(args)

	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		log.Fatalf("keygen: %v", err)
	}
	if err := os.WriteFile(*out, []byte(base64.StdEncoding.EncodeToString(priv)+"\n"), 0o600); err != nil {
		log.Fatalf("keygen: write key: %v", err)
	}
	pubPath := *out + ".pub"
	if err := os.WriteFile(pubPath, []byte(base64.StdEncoding.EncodeToString(pub)+"\n"), 0o644); err != nil {
		log.Fatalf("keygen: write public key: %v", err)
	}
	fmt.Printf("private: %s (0600)\npublic:  %s\n", *out, pubPath)
	fmt.Printf("\ntrust key for central config:\n  %s: %s\n", filepath.Base(pubPath), base64.StdEncoding.EncodeToString(pub))
}

func signCmd(args []string) {
	fs := flag.NewFlagSet("sign", flag.ExitOnError)
	root := fs.String("root", "", "agent workspace root to inventory (required)")
	keyPath := fs.String("key", "agentids.key", "private key file")
	agentID := fs.String("agent", "", "agent id (required)")
	out := fs.String("out", "manifest.json", "manifest output path")
	include := fs.String("include", "SOUL.md,IDENTITY.md,USER.md,TOOLS.md,MEMORY.md,HEARTBEAT.md,AGENTS.md,skills/**/*.md", "comma-separated globs")
	_ = fs.Parse(args)

	if *root == "" || *agentID == "" {
		fs.Usage()
		os.Exit(2)
	}

	files, err := InventoryRoot(*root, splitCSV(*include))
	if err != nil {
		log.Fatalf("sign: %v", err)
	}
	if len(files) == 0 {
		log.Fatalf("sign: no files matched under %s (check -include)", *root)
	}

	m := provenance.BuildManifest(*agentID, *root, files)

	priv, err := loadKey(*keyPath)
	if err != nil {
		log.Fatalf("sign: %v", err)
	}
	if err := m.Sign(priv); err != nil {
		log.Fatalf("sign: %v", err)
	}
	if err := m.SaveManifest(*out); err != nil {
		log.Fatalf("sign: %v", err)
	}
	fmt.Printf("signed manifest for %q: %d files -> %s\n", *agentID, len(m.Files), *out)
}

func verifyCmd(args []string) {
	fs := flag.NewFlagSet("verify", flag.ExitOnError)
	root := fs.String("root", "", "agent workspace root to check (required)")
	manifestPath := fs.String("manifest", "manifest.json", "manifest to verify against")
	pubPath := fs.String("pub", "", "signer public key (base64 file); required to verify the signature")
	include := fs.String("include", "", "comma-separated globs (default: manifest paths)")
	_ = fs.Parse(args)

	if *root == "" {
		fs.Usage()
		os.Exit(2)
	}

	m, err := provenance.LoadManifest(*manifestPath)
	if err != nil {
		log.Fatalf("verify: %v", err)
	}

	if *pubPath != "" {
		pub, err := loadPub(*pubPath)
		if err != nil {
			log.Fatalf("verify: %v", err)
		}
		if err := m.Verify(pub); err != nil {
			log.Fatalf("verify: manifest signature invalid: %v", err)
		}
		fmt.Println("manifest signature: OK")
	} else {
		fmt.Fprintln(os.Stderr, "WARNING: no -pub given; signature not checked")
	}

	globs := splitCSV(*include)
	var files map[string]string
	if len(globs) == 0 {
		// Default scan: WALK the workspace with the standard include set. This is
		// what catches a brand-new dropped skill — a glob of the manifest's own
		// paths would never look inside a new directory.
		files, err = WalkRoot(*root, DefaultInclude, DefaultExclude)
	} else {
		files, err = InventoryRoot(*root, globs)
	}
	if err != nil {
		log.Fatalf("verify: %v", err)
	}

	verdicts := provenance.Verify(m, files)
	bad := 0
	for _, v := range verdicts {
		if v.Status != provenance.TrustOK {
			fmt.Printf("%-8s %s\n", v.Status, v.Path)
			bad++
		}
	}
	if bad == 0 {
		fmt.Printf("workspace clean: %d files match the approved manifest\n", len(files))
		return
	}
	os.Exit(1)
}

func splitCSV(s string) []string {
	if s == "" {
		return nil
	}
	var out []string
	for _, p := range splitComma(s) {
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

func splitComma(s string) []string {
	var out []string
	cur := ""
	for _, r := range s {
		if r == ',' {
			out = append(out, trimSpace(cur))
			cur = ""
			continue
		}
		cur += string(r)
	}
	out = append(out, trimSpace(cur))
	return out
}

func trimSpace(s string) string {
	start, end := 0, len(s)
	for start < end && (s[start] == ' ' || s[start] == '\t' || s[start] == '\n' || s[start] == '\r') {
		start++
	}
	for end > start && (s[end-1] == ' ' || s[end-1] == '\t' || s[end-1] == '\n' || s[end-1] == '\r') {
		end--
	}
	return s[start:end]
}

func loadKey(path string) (ed25519.PrivateKey, error) { return provenance.LoadPrivateKey(path) }

func loadPub(path string) (ed25519.PublicKey, error) { return provenance.LoadPublicKey(path) }
