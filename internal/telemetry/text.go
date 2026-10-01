package telemetry

import (
	"bufio"
	"fmt"
	"io"
	"strings"

	"github.com/CRPerdue/watchman/internal/schema"
)

// ParseTextStream reads a line-oriented execution stream, one process per line:
//
//	/path/to/exe<TAB>arg1<TAB>arg2...
//
// or, with no tabs, whitespace-split. It exists for tests, demos, and any
// pipeline that can produce "what ran" without auditd. It is deliberately
// dumb: no quoting, no shell parsing. If you need fidelity, use auditd.
func ParseTextStream(r io.Reader) ([]schema.Action, error) {
	var out []schema.Action
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimRight(sc.Text(), "\r")
		if line == "" || strings.HasPrefix(strings.TrimSpace(line), "#") {
			continue
		}
		parts := strings.Split(line, "\t")
		if len(parts) == 1 {
			parts = strings.Fields(line)
		}
		if len(parts) == 0 || parts[0] == "" {
			continue
		}
		exe := parts[0]
		argv := make([]any, 0, len(parts))
		for _, p := range parts {
			argv = append(argv, p)
		}
		out = append(out, schema.Action{
			Type: ProcessExec,
			Detail: map[string]any{
				"exe":  exe,
				"argv": argv,
			},
		})
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("telemetry: read text stream: %w", err)
	}
	return out, nil
}
