package main

import (
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"text/tabwriter"

	"github.com/CRPerdue/agentids/internal/rules"
)

// defaultPackPath is used when no path is given: the in-repo pack directory.
const defaultPackPath = "rules"

// loadSet loads every rule pack named by paths. A path may be a pack file
// (*.yaml/*.yml) or a directory of packs. Rule ids are required to be unique
// across the whole set, not just within one pack.
func loadSet(paths []string) ([]*rules.Pack, error) {
	if len(paths) == 0 {
		paths = []string{defaultPackPath}
	}
	var packs []*rules.Pack
	for _, p := range paths {
		info, err := os.Stat(p)
		if err != nil {
			return nil, fmt.Errorf("rules: %w", err)
		}
		if info.IsDir() {
			ps, err := rules.LoadPacks(p)
			if err != nil {
				return nil, err
			}
			packs = append(packs, ps...)
			continue
		}
		pack, err := rules.LoadPack(p)
		if err != nil {
			return nil, err
		}
		packs = append(packs, pack)
	}
	if err := rules.CheckUniqueIDs(packs); err != nil {
		return nil, err
	}
	return packs, nil
}

// summarize prints a one-screen summary of a loaded pack set.
func summarize(out io.Writer, packs []*rules.Pack) {
	sev := map[string]int{}
	tech := map[string]bool{}
	total := 0
	for _, p := range packs {
		for i := range p.Rules {
			r := p.Rules[i]
			total++
			sev[r.Severity]++
			if r.Technique != "" {
				tech[r.Technique] = true
			}
		}
	}
	fmt.Fprintf(out, "ok: %d pack(s), %d rule(s)\n", len(packs), total)
	order := []string{"critical", "high", "medium", "low", "info"}
	var parts []string
	for _, s := range order {
		if sev[s] > 0 {
			parts = append(parts, fmt.Sprintf("%s=%d", s, sev[s]))
		}
	}
	fmt.Fprintf(out, "   severity: %s\n", strings.Join(parts, " "))
	fmt.Fprintf(out, "   techniques: %d distinct\n", len(tech))
	for _, p := range packs {
		fmt.Fprintf(out, "   pack %s v%s: %d rule(s)\n", p.Name, orDash(p.Version), len(p.Rules))
	}
}

// listRules prints every rule in the set, sorted by id, one per line.
func listRules(out io.Writer, packs []*rules.Pack) {
	var rows []rules.Rule
	for _, p := range packs {
		rows = append(rows, p.Rules...)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].ID < rows[j].ID })
	w := tabwriter.NewWriter(out, 0, 2, 2, ' ', 0)
	fmt.Fprintln(w, "ID\tSEVERITY\tTECHNIQUE\tTITLE")
	for _, r := range rows {
		fmt.Fprintf(w, "%s\t%s\t%s\t%s\n", r.ID, r.Severity, orDash(r.Technique), r.Title)
	}
	_ = w.Flush()
}

func orDash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}
