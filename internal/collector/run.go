package collector

import (
	"fmt"
	"path/filepath"
	"sort"
)

// Collected is one agent root's inventory, tagged with the resolved root path.
type Collected struct {
	Root      string
	Inventory *Inventory
}

// CollectAll walks every agent root in the config and returns the inventories.
// Root paths may contain glob metacharacters (e.g. /home/*/.openclaw/workspace-*);
// each match is treated as one agent root. Deterministic order.
func CollectAll(cfg *Config) ([]Collected, error) {
	var out []Collected
	for _, ar := range cfg.AgentRoots {
		matches, err := filepath.Glob(ar.Path)
		if err != nil {
			return nil, fmt.Errorf("bad agent root glob %q: %w", ar.Path, err)
		}
		// A literal path with no glob chars that exists comes back as one match;
		// a path that matches nothing and has no glob chars is likely a misconfig.
		if len(matches) == 0 {
			return nil, fmt.Errorf("agent root %q matched no directories", ar.Path)
		}
		for _, root := range matches {
			inv, err := WalkRoot(root, ar.Include, ar.Exclude)
			if err != nil {
				return nil, fmt.Errorf("walk %q: %w", root, err)
			}
			out = append(out, Collected{Root: root, Inventory: inv})
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Root < out[j].Root })
	return out, nil
}
