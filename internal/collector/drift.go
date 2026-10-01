package collector

import (
	"fmt"
	"strings"

	"github.com/CRPerdue/watchman/internal/schema"
)

// Drift compares a stored baseline inventory to a fresh one and returns one
// schema.Event per changed/added/removed file. This is the Layer-1 core: it
// turns "a markdown file changed" into a pageable, attributable event.
//
// baseline may be nil (first run) — in that case there is nothing to diff
// against and the caller should record `current` as the new baseline instead.
func Drift(host schema.Host, agent schema.Agent, baseline, current *Inventory, maxDiffBytes int) []schema.Event {
	if current == nil {
		return nil
	}
	base := map[string]FileState{}
	if baseline != nil {
		for _, f := range baseline.Files {
			base[f.Path] = f
		}
	}
	curr := map[string]FileState{}
	for _, f := range current.Files {
		curr[f.Path] = f
	}

	events := []schema.Event{}

	// Changed or removed: iterate baseline.
	for path, b := range base {
		c, ok := curr[path]
		if !ok {
			// Removed file.
			ev := schema.NewEnvelope(schema.EventIdentityDrift, host)
			ev.Agent = &agent
			ev.File = &schema.FileInfo{
				Path:         path,
				SHA256Before: b.SHA256,
				SizeBefore:   ptr(b.Size),
			}
			if maxDiffBytes != 0 {
				ev.File.Diff = UnifiedDiff(path, b.Content, "", maxDiffBytes)
			}
			ev.Severity = severityFor(path)
			events = append(events, *ev)
			continue
		}
		if c.SHA256 == b.SHA256 {
			continue // unchanged
		}
		ev := schema.NewEnvelope(schema.EventIdentityDrift, host)
		ev.Agent = &agent
		ev.File = &schema.FileInfo{
			Path:         path,
			SHA256Before: b.SHA256,
			SHA256After:  c.SHA256,
			SizeBefore:   ptr(b.Size),
			SizeAfter:    ptr(c.Size),
		}
		if maxDiffBytes != 0 {
			ev.File.Diff = UnifiedDiff(path, b.Content, c.Content, maxDiffBytes)
		}
		ev.Severity = severityFor(path)
		events = append(events, *ev)
	}

	// Added: iterate current.
	for path, c := range curr {
		if _, ok := base[path]; ok {
			continue
		}
		ev := schema.NewEnvelope(schema.EventIdentityDrift, host)
		ev.Agent = &agent
		ev.File = &schema.FileInfo{
			Path:        path,
			SHA256After: c.SHA256,
			SizeAfter:   ptr(c.Size),
		}
		if maxDiffBytes != 0 {
			ev.File.Diff = UnifiedDiff(path, "", c.Content, maxDiffBytes)
		}
		ev.Severity = severityFor(path)
		events = append(events, *ev)
	}

	return events
}

// severityFor assigns a default severity by filename. Persona/identity files
// are high; everything else medium. Configurable per baseline in a later pass.
func severityFor(path string) string {
	p := strings.ToLower(path)
	switch {
	case strings.HasSuffix(p, "soul.md"),
		strings.HasSuffix(p, "identity.md"),
		strings.HasSuffix(p, "persona.md"):
		return schema.SeverityHigh
	default:
		return schema.SeverityMedium
	}
}

func ptr(i int64) *int64 { return &i }

// UnifiedDiff produces a minimal line-level unified diff between two text blobs.
// Deliberately tiny and dependency-free — sufficient for "what changed in this
// markdown persona file" and safe to ship in a public repo. Returns the diff
// truncated to maxBytes (0 = no truncation).
func UnifiedDiff(path, before, after string, maxBytes int) string {
	bl := strings.Split(before, "\n")
	al := strings.Split(after, "\n")

	var sb strings.Builder
	fmt.Fprintf(&sb, "--- a/%s\n+++ b/%s\n", path, path)

	// Simple LCS-free line walk: emit removals then additions for the changed
	// span. Good enough for small config/persona files; swap for a real Myers
	// diff if files grow large.
	n := len(bl)
	if len(al) < n {
		n = len(al)
	}
	i := 0
	for i < n && bl[i] == al[i] {
		i++
	}
	// Common head length = i.
	bTail := bl[i:]
	aTail := al[i:]
	// Trim common tail.
	j := 0
	for j < len(bTail) && j < len(aTail) && bTail[len(bTail)-1-j] == aTail[len(aTail)-1-j] {
		j++
	}
	bMid := bTail[:len(bTail)-j]
	aMid := aTail[:len(aTail)-j]

	fmt.Fprintf(&sb, "@@ -%d,%d +%d,%d @@\n", i+1, len(bMid), i+1, len(aMid))
	for _, l := range bMid {
		sb.WriteString("-" + l + "\n")
	}
	for _, l := range aMid {
		sb.WriteString("+" + l + "\n")
	}

	out := sb.String()
	if maxBytes > 0 && len(out) > maxBytes {
		out = out[:maxBytes] + "\n... (truncated)\n"
	}
	return out
}
