package provenance

import (
	"github.com/CRPerdue/watchman/internal/schema"
)

// Events converts provenance verdicts into Layer 2 events. Only non-OK verdicts
// produce events; a clean file is not news.
//
// The manifest signer is recorded so an operator reading an alert knows whether
// the file was untrusted because it was unlisted or because it failed against a
// signature that itself could not be trusted.
func Events(host schema.Host, agent schema.Agent, verdicts []Verdict) []schema.Event {
	var out []schema.Event
	for _, v := range verdicts {
		if v.Status == TrustOK {
			continue
		}
		ev := schema.NewEnvelope(schema.EventProvenanceUntrusted, host)
		a := agent
		ev.Agent = &a
		ev.Severity = SeverityFor(v)
		ev.File = &schema.FileInfo{
			Path:         v.Path,
			SHA256Before: v.HashManifest, // approved hash (may be empty if unlisted)
			SHA256After:  v.HashHost,     // on-host hash (may be empty if missing)
		}
		out = append(out, *ev)
	}
	return out
}
