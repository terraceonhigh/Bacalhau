// Package git provides helpers for running git commands against a project.
package git

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

// Root finds the .git directory. It checks chaptersDir itself, then its
// parent. Returns the directory containing .git, or "".
func Root(chaptersDir string) string {
	if chaptersDir != "" {
		if info, err := os.Stat(filepath.Join(chaptersDir, ".git")); err == nil && info.IsDir() {
			return chaptersDir
		}
	}
	parent := filepath.Dir(chaptersDir)
	if parent != "" && parent != chaptersDir {
		if info, err := os.Stat(filepath.Join(parent, ".git")); err == nil && info.IsDir() {
			return parent
		}
	}
	return ""
}

// RunGit executes a git command with a 10-second timeout and returns
// (returncode, stdout, stderr). A returncode of -1 indicates git is not
// installed or the command timed out.
func RunGit(chaptersDir string, args ...string) (int, string, string) {
	cwd := Root(chaptersDir)
	if cwd == "" {
		cwd = chaptersDir
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir = cwd

	stdout, err := cmd.Output()
	var stderr string
	if ee, ok := err.(*exec.ExitError); ok {
		stderr = string(ee.Stderr)
		return ee.ExitCode(), string(stdout), stderr
	}
	if err != nil {
		// git not found or timeout
		if ctx.Err() == context.DeadlineExceeded {
			return -1, "", "git command timed out"
		}
		return -1, "", "git is not installed"
	}
	return 0, string(stdout), ""
}

// Installed returns true if git is available on the system PATH.
func Installed() bool {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", "--version")
	return cmd.Run() == nil
}

// HasCommits returns true if the repository has at least one commit.
func HasCommits(chaptersDir string) bool {
	rc, _, _ := RunGit(chaptersDir, "rev-parse", "HEAD")
	return rc == 0
}

// ResolvePath resolves a display path (relative to the project scope) back to
// a git-root-relative path. This mirrors the Python _git_resolve_path logic.
func ResolvePath(chaptersDir, shortPath string) string {
	root := Root(chaptersDir)
	if root == "" || chaptersDir == "" {
		return shortPath
	}

	scope := chaptersDir
	parent := filepath.Dir(chaptersDir)
	if parent != "" && parent != root {
		scope = parent
	}

	if scope == root {
		return shortPath
	}

	relPrefix, err := filepath.Rel(root, scope)
	if err != nil {
		return shortPath
	}
	return filepath.Join(relPrefix, shortPath)
}
