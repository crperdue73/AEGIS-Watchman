package main

import (
	"os"
	"path/filepath"
	"testing"
)

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestWalkRootFindsNestedNewSkill(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "SOUL.md"), "soul")
	writeFile(t, filepath.Join(root, "IDENTITY.md"), "id")
	writeFile(t, filepath.Join(root, "skills", "tidy", "SKILL.md"), "tidy")
	// A brand-new dropped skill in a directory nobody globbed for.
	writeFile(t, filepath.Join(root, "skills", "c2", "SKILL.md"), "exfil")
	// Noise that must be excluded or ignored.
	writeFile(t, filepath.Join(root, ".git", "config"), "gitstuff")
	writeFile(t, filepath.Join(root, "image.png"), "binary")
	writeFile(t, filepath.Join(root, "notes.txt"), "not an agent file")

	files, err := WalkRoot(root, DefaultInclude, DefaultExclude)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"SOUL.md", "IDENTITY.md", "skills/tidy/SKILL.md", "skills/c2/SKILL.md"} {
		if _, ok := files[want]; !ok {
			t.Errorf("walk missed %q; got %v", want, keys(files))
		}
	}
	if _, ok := files[".git/config"]; ok {
		t.Error("walk descended into .git")
	}
	if _, ok := files["image.png"]; ok {
		t.Error("walk included a non-matching extension")
	}
	if _, ok := files["notes.txt"]; ok {
		t.Error("walk included .txt (not in DefaultInclude)")
	}
}

func TestInventoryRootGlobs(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "SOUL.md"), "soul")
	writeFile(t, filepath.Join(root, "skills", "tidy", "SKILL.md"), "tidy")

	files, err := InventoryRoot(root, []string{"SOUL.md", "skills/**/*.md"})
	if err != nil {
		t.Fatal(err)
	}
	if len(files) != 2 {
		t.Errorf("expected 2 files, got %v", keys(files))
	}
	// Paths are workspace-relative and slash-normalized.
	if _, ok := files["skills/tidy/SKILL.md"]; !ok {
		t.Errorf("expected slash-relative path, got %v", keys(files))
	}
}

func TestInventoryRootMissingRootErrors(t *testing.T) {
	if _, err := InventoryRoot("/definitely/not/here", []string{"*.md"}); err == nil {
		t.Error("expected error for a missing root")
	}
}

func TestInventoryRootNotADirectory(t *testing.T) {
	f := filepath.Join(t.TempDir(), "file.md")
	writeFile(t, f, "x")
	if _, err := InventoryRoot(f, []string{"*.md"}); err == nil {
		t.Error("expected error when root is a file")
	}
}

func TestWalkRootExcludesDirs(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "node_modules", "pkg", "SKILL.md"), "dep")
	writeFile(t, filepath.Join(root, "SOUL.md"), "soul")
	files, err := WalkRoot(root, DefaultInclude, DefaultExclude)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := files["node_modules/pkg/SKILL.md"]; ok {
		t.Error("node_modules should be excluded")
	}
	if _, ok := files["SOUL.md"]; !ok {
		t.Error("SOUL.md missing")
	}
}

func TestMatchesAny(t *testing.T) {
	cases := []struct {
		rel  string
		want bool
	}{
		{"SOUL.md", true},
		{"skills/tidy/SKILL.md", true},
		{"deep/nested/thing.json", true},
		{"notes.txt", false},
		{".git/config", false},
	}
	for _, c := range cases {
		if got := matchesAny(c.rel, DefaultInclude); got != c.want {
			t.Errorf("matchesAny(%q) = %v, want %v", c.rel, got, c.want)
		}
	}
}

func keys(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
