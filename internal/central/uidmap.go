package central

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/CRPerdue/agentids/internal/schema"
)

// UidResolver maps numeric uids to human usernames so an alert names a person
// ("user=deploy uid=1000") instead of a bare numeric id ("uid:1000").
//
// Why this lives on central and not the collector: the collector's hot path
// must stay dependency-free and must not fail because /etc/passwd was
// unreadable at the moment of an event. auditd hands us a uid; resolving it to
// a name is a presentation concern, it can be done once at the edge, and
// central is where every alert is actually built. Offloading it here also means
// one passwd file describes a fleet, instead of one per host.
//
// Resolution is best-effort by design. An unknown uid stays numeric — a wrong
// name is worse than no name, and a missing name must never cost us the alert.
type UidResolver struct {
	byUID map[int]string
}

// NewUidResolver returns a resolver backed by the given map. A nil/empty map
// yields a resolver that resolves nothing (every uid stays numeric), so callers
// need no nil checks.
func NewUidResolver(byUID map[int]string) *UidResolver {
	m := make(map[int]string, len(byUID))
	for k, v := range byUID {
		m[k] = v
	}
	return &UidResolver{byUID: m}
}

// LoadUidMapFromPasswd reads a passwd-style file (name:passwd:uid:gid:...) and
// returns a uid->name map. Blank lines and comments (#) are skipped; malformed
// lines are ignored rather than fatal so one odd entry cannot void the whole
// map. A missing file is an error (the operator asked for it), but the caller
// decides whether that is fatal.
func LoadUidMapFromPasswd(path string) (map[int]string, error) {
	fh, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("uid map: open %q: %w", path, err)
	}
	defer fh.Close()

	out := map[int]string{}
	sc := bufio.NewScanner(fh)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		fields := strings.Split(line, ":")
		if len(fields) < 3 {
			continue // not a passwd line
		}
		name := fields[0]
		uid, err := strconv.Atoi(fields[2])
		if err != nil || name == "" {
			continue
		}
		out[uid] = name
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("uid map: read %q: %w", path, err)
	}
	return out, nil
}

// Resolve returns the username for a uid, or "" if unknown.
func (r *UidResolver) Resolve(uid int) string {
	if r == nil {
		return ""
	}
	return r.byUID[uid]
}

// Enabled reports whether the resolver can name anything.
func (r *UidResolver) Enabled() bool {
	return r != nil && len(r.byUID) > 0
}

// Apply rewrites an event's actor so its User field is a name when we can name
// it. It is idempotent and safe on a nil actor. The numeric UID is preserved:
// the name is added alongside the id, never instead of it, because the id is
// the ground truth and a name can be reassigned.
func (r *UidResolver) Apply(ev *schema.Event) {
	if r == nil || !r.Enabled() || ev == nil || ev.Actor == nil {
		return
	}
	if ev.Actor.UID == nil {
		return
	}
	name := r.Resolve(*ev.Actor.UID)
	if name == "" {
		return
	}
	ev.Actor.User = name
}
