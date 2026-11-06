package rules

// pathfixture_engine_test.go — the 77 pathfixture rows, judged by the COMPILED engine.
//
// Beat 776 corrected a file that had said "never been run" for three weeks. The
// follow-up question is not "are there other stale notes" but "which verdicts in
// this tree were produced by a Python mirror and have never been put to the
// compiled engine?" `scripts/pathfixture.py` is the largest one: 77 rows across
// the whole `path_glob_any` family, every verdict produced by a port.
//
// This file is the Go bridge that probe never had. It re-derives no expectation.
// It reads the same (glob, path, want) table the Python probe asserts and asks
// the real Engine the same question.
//
// WHAT A GREEN HERE MEANS: the mirror is a faithful oracle for these 77 inputs,
// measured rather than hoped. WHAT IT DOES NOT MEAN: that the engine's behaviour
// is DESIRABLE. Twelve rows are declared XFAIL in the probe because the engine
// over-matches (the contains-fallback on paths — see
// docs/FINDING-2026-10-28-contains-fallback-on-paths.md). `want` is what the RULE
// SHOULD DO; xfailRows are where Go is expected to disagree with it.
//
// BEAT 777 — WHY THE TABLE IS GENERATED. The first version of this file hand-copied
// the rows. I typed 76 instead of 77, invented `*/etc/hosts` (a row that does not
// exist in the probe), and wrote an xfail map with 3 entries when the real one has
// 12. The suite then reported "9 undeclared disagreements" — true, and entirely my
// copy error. A test whose failing output cannot be told apart from a real engine
// finding is worse than no test. So `scripts/pathtable.py` renders the block below
// from the Python source, and `--check` goes red when the two drift.

import (
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// pathRow is one row of the probe's FIXTURES table: (glob, path, want).
type pathRow struct {
	glob string
	path string
	want bool
}

// pathFire builds a single-rule engine whose only predicate is path_glob_any on a
// file.access action, and reports whether the path fires it.
func pathFire(t *testing.T, glob, path string) bool {
	t.Helper()
	p := mustPack(t, `
name: path-semantics
rules:
  - id: path.semantics.probe
    severity: medium
    match:
      type: file.access
      path_glob_any: ["`+glob+`"]
`)
	e := NewEngine(p)
	evs := e.Match(
		schema.Host{ID: "h1"}, nil,
		schema.Action{Type: "file.access", Detail: map[string]any{"path": path}},
		nil, timeNow(),
	)
	return len(evs) > 0
}

// TestPathFixtureAgreesWithCompiledEngine is the bridge. The row count comes from
// the generator, so a truncated table cannot pass: len(pathRows) and pathRowCount
// are produced by the same render pass, and a hand edit to one goes red.
func TestPathFixtureAgreesWithCompiledEngine(t *testing.T) {
	if len(pathRows) != pathRowCount {
		t.Fatalf("pathRows has %d rows, generator says %d — the table was edited by hand",
			len(pathRows), pathRowCount)
	}
	var diffs int
	for _, r := range pathRows {
		got := pathFire(t, r.glob, r.path)
		if got == r.want {
			continue
		}
		key := [2]string{r.glob, r.path}
		if xfailRows[key] {
			continue // declared: engine differs from the rule's intent, on purpose, visibly
		}
		diffs++
		t.Errorf("MIRROR/ENGINE DISAGREE glob=%q path=%q: python said want=%v, Go says %v",
			r.glob, r.path, r.want, got)
	}
	if diffs > 0 {
		t.Errorf("%d undeclared disagreements — the Python mirror is not a faithful oracle for these rows", diffs)
	}
}

// TestXfailRowsStillMisfire keeps the declared XFAILs honest in the direction that
// matters: if the engine is FIXED (the contains-fallback stops firing on paths),
// these rows start agreeing with `want` and this test goes red — telling us to
// promote them, rather than leaving a stale exemption forever. Same discipline as
// beat 776's skip→assert conversion.
func TestXfailRowsStillMisfire(t *testing.T) {
	var found int
	for _, r := range pathRows {
		key := [2]string{r.glob, r.path}
		if !xfailRows[key] {
			continue
		}
		found++
		if pathFire(t, r.glob, r.path) == r.want {
			t.Errorf("declared XFAIL now AGREES with intent: glob=%q path=%q — the engine was "+
				"fixed. Promote this row out of xfailRows (and update the Python probe).", r.glob, r.path)
		}
	}
	if found != pathXfailCount {
		t.Errorf("found %d of %d declared XFAIL rows in the table — the exemption map and the "+
			"table have drifted", found, pathXfailCount)
	}
}

// BEGIN GENERATED TABLE — scripts/pathtable.py
// Generated from scripts/pathfixture.py by scripts/pathtable.py — DO NOT EDIT BY HAND.

var pathRows = []pathRow{
	{glob: "*/.ssh/*", path: "./.ssh/id_rsa", want: true},
	{glob: "*/.ssh/*", path: "/home/agent/.ssh/id_rsa/", want: true},
	{glob: "*/.aws/*", path: "./.aws/credentials", want: true},
	{glob: "*/.aws/*", path: ".aws/credentials", want: false},
	{glob: "*/.config/gcloud/*", path: "./.config/gcloud/key.json", want: true},
	{glob: "*/.config/gcloud/application_default_credentials.json*", path: "./.config/gcloud/application_default_credentials.json", want: true},
	{glob: "*/.config/openai*", path: "./.config/openai", want: true},
	{glob: "*/.docker/config.json*", path: "./.docker/config.json", want: true},
	{glob: "*/.git-credentials*", path: "./.git-credentials", want: true},
	{glob: "*/.kube/config*", path: "./.kube/config", want: true},
	{glob: "*/.netrc*", path: "./.netrc", want: true},
	{glob: "*/.zshrc", path: "./.zshrc", want: true},
	{glob: "*/.zshrc", path: ".zshrc", want: false},
	{glob: "*/.bash_profile", path: "./.bash_profile", want: true},
	{glob: "*.env", path: "./.env", want: true},
	{glob: "*.env", path: "prod.env", want: true},
	{glob: "*/.bashrc", path: "./.bashrc", want: true},
	{glob: "*/.bashrc*", path: "./.bashrc", want: true},
	{glob: "*/authorized_keys", path: "./authorized_keys", want: true},
	{glob: "*id_rsa*", path: "./keys/id_rsa", want: true},
	{glob: "*id_ed25519*", path: "./keys/id_ed25519", want: true},
	{glob: "*credentials*", path: "./credentials.json", want: true},
	{glob: "*/openai*key*", path: "./openai_api_key.txt", want: true},
	{glob: "*anthropic*key*", path: "./anthropic_api_key.txt", want: true},
	{glob: "*/etc/crontab", path: "./etc/crontab", want: true},
	{glob: "*/etc/crontab", path: "etc/crontab", want: false},
	{glob: "*/etc/cron.d/*", path: "./etc/cron.d/evil", want: true},
	{glob: "*/etc/cron.d/*", path: "/etc/cron.d/evil", want: true},
	{glob: "*/etc/cron.daily/*", path: "./etc/cron.daily/evil", want: true},
	{glob: "*/etc/cron.hourly/*", path: "./etc/cron.hourly/evil", want: true},
	{glob: "*/etc/systemd/system/*", path: "./etc/systemd/system/evil.service", want: true},
	{glob: "*/etc/systemd/system/*", path: "/etc/systemd/system/evil.service", want: true},
	{glob: "*/.profile", path: "./.profile", want: true},
	{glob: "*/.profile", path: "/home/agent/.profile", want: true},
	{glob: "*/.ssh/*", path: "/home/agent/.ssh/id_rsa.pub", want: true},
	{glob: "*/.ssh/*", path: "/home/agent/.ssh", want: false},
	{glob: "*/.ssh/*", path: "/home/agent/.ssh/", want: false},
	{glob: "*/.aws/*", path: "/home/agent/.aws", want: false},
	{glob: "*/.aws/*", path: "/home/agent/.aws/", want: false},
	{glob: "*.env", path: "environment.txt", want: false},
	{glob: "*.env", path: "env", want: false},
	{glob: "*/.kube/config*", path: "/home/agent/.kube/other", want: false},
	{glob: "*/.netrc*", path: "/home/agent/.netrcfile/example", want: false},
	{glob: "*/.zshrc", path: "/home/agent/.zshrc.bak", want: false},
	{glob: "*/.bashrc", path: "/home/agent/.bashrc.bak", want: false},
	{glob: "*/.git-credentials*", path: "/home/agent/.git-credentials-backup/x", want: false},
	{glob: "*/etc/crontab", path: "/etc/crontab.local", want: false},
	{glob: "*/etc/cron.d/*", path: "/etc/cron.d", want: false},
	{glob: "*/etc/cron.d/*", path: "/etc/cron.daily/evil", want: false},
	{glob: "*/.config/gcloud/*", path: "/etc/config/gcloud/x", want: false},
	{glob: "*/.netrc*", path: ".netrc", want: false},
	{glob: "*/.bashrc", path: ".bashrc", want: false},
	{glob: "*/.profile", path: ".profile", want: false},
	{glob: "*/.ssh/*", path: ".ssh/id_rsa", want: false},
	{glob: "*/.aws/*", path: ".aws/credentials", want: false},
	{glob: "*/.kube/config*", path: ".kube/config", want: false},
	{glob: "*/.git-credentials*", path: ".git-credentials", want: false},
	{glob: "*/.docker/config.json*", path: ".docker/config.json", want: false},
	{glob: "*/etc/crontab", path: "etc/crontab", want: false},
	{glob: "*/.netrc*", path: "./.netrc", want: true},
	{glob: "*/.netrc*", path: "/home/u/.netrc", want: true},
	{glob: "*/.bashrc", path: "/root/.bashrc", want: true},
	{glob: "*/.ssh/*", path: "/home/u/.ssh/id_rsa", want: true},
	{glob: "*/.config/gcloud/application_default_credentials.json*", path: "./.config/gcloud/README.md", want: false},
	{glob: "*/.config/openai*", path: "./.config/openaiX", want: false},
	{glob: "*/.config/openai*", path: "./.config/azure/config", want: false},
	{glob: "*/.bash_profile", path: "./.bash_profile.bak", want: false},
	{glob: "*/.bashrc*", path: "./.bash_history", want: false},
	{glob: "*/authorized_keys", path: "./authorized_keys.bak", want: false},
	{glob: "*id_rsa*", path: "./keys/id_rsa_public_readme.md", want: false},
	{glob: "*id_ed25519*", path: "./notes/ed25519.txt", want: false},
	{glob: "*credentials*", path: "./credentials_help.html", want: false},
	{glob: "*/openai*key*", path: "./.config/openai/models.json", want: false},
	{glob: "*anthropic*key*", path: "./.anthropic/README.md", want: false},
	{glob: "*/etc/cron.daily/*", path: "./etc/cron.weekly/job", want: false},
	{glob: "*/etc/cron.hourly/*", path: "./etc/cron.daily/job", want: false},
	{glob: "*/etc/systemd/system/*", path: "./etc/systemd/user/evil.service", want: false},
}

var xfailRows = map[[2]string]bool{
	{"*/.ssh/*", "/home/agent/.ssh/"}:                                true,
	{"*/.aws/*", "/home/agent/.aws/"}:                                true,
	{"*/.netrc*", "/home/agent/.netrcfile/example"}:                  true,
	{"*/.zshrc", "/home/agent/.zshrc.bak"}:                           true,
	{"*/.bashrc", "/home/agent/.bashrc.bak"}:                         true,
	{"*/.git-credentials*", "/home/agent/.git-credentials-backup/x"}: true,
	{"*/etc/crontab", "/etc/crontab.local"}:                          true,
	{"*/.config/openai*", "./.config/openaiX"}:                       true,
	{"*/.bash_profile", "./.bash_profile.bak"}:                       true,
	{"*/authorized_keys", "./authorized_keys.bak"}:                   true,
	{"*id_rsa*", "./keys/id_rsa_public_readme.md"}:                   true,
	{"*credentials*", "./credentials_help.html"}:                     true,
}

const pathRowCount = 77
const pathXfailCount = 12

// END GENERATED TABLE
