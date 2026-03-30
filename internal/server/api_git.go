package server

import (
	"net/http"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/terraceonhigh/Bacalhau/internal/git"
)

var shaRe = regexp.MustCompile(`\[[\w/.-]+ ([a-f0-9]+)\]`)

// gitScope returns the directory to scope git operations to.
// If the parent of chaptersDir is different from the git root, we scope to the
// parent (to include project-level files like _order.yaml).
func (s *Server) gitScope() string {
	chapDir := s.state.ChaptersDir()
	scope := chapDir
	parent := filepath.Dir(chapDir)
	root := git.Root(chapDir)
	if parent != "" && parent != root {
		scope = parent
	}
	return scope
}

func (s *Server) gitStatus(w http.ResponseWriter, _ *http.Request) {
	chapDir := s.state.ChaptersDir()
	installed := git.Installed()
	root := git.Root(chapDir)
	isTemp := false
	if td := s.state.TempDir(); td != "" && chapDir != "" && strings.HasPrefix(chapDir, td) {
		isTemp = true
	}

	if !installed {
		sendJSON(w, http.StatusOK, map[string]any{
			"git_installed": false, "is_repo": false, "is_temp": isTemp, "files": []any{},
		})
		return
	}
	if root == "" {
		sendJSON(w, http.StatusOK, map[string]any{
			"git_installed": true, "is_repo": false, "is_temp": isTemp, "files": []any{},
		})
		return
	}

	scope := s.gitScope()
	rc, out, errStr := git.RunGit(chapDir, "status", "--porcelain=v1", "-uall", "--", scope)
	if rc != 0 {
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": strings.TrimSpace(errStr)})
		return
	}

	// Compute relative prefix for shorter display paths.
	relPrefix := ""
	if root != "" && scope != "" && strings.HasPrefix(scope, root) {
		rel, err := filepath.Rel(root, scope)
		if err == nil && rel != "." {
			relPrefix = rel
		}
	}

	var files []map[string]any
	for _, line := range strings.Split(out, "\n") {
		if len(line) < 4 {
			continue
		}
		indexStatus := line[0]
		worktreeStatus := line[1]
		path := line[3:]

		// Handle renamed files.
		if idx := strings.Index(path, " -> "); idx >= 0 {
			path = path[idx+4:]
		}

		// Strip scope prefix.
		if relPrefix != "" && strings.HasPrefix(path, relPrefix+"/") {
			path = path[len(relPrefix)+1:]
		}

		if indexStatus != ' ' && indexStatus != '?' {
			files = append(files, map[string]any{
				"path": path, "status": string(indexStatus), "staged": true,
			})
		}
		if worktreeStatus != ' ' && worktreeStatus != 0 {
			st := string(worktreeStatus)
			if worktreeStatus == '?' {
				st = "?"
			}
			files = append(files, map[string]any{
				"path": path, "status": st, "staged": false,
			})
		}
	}

	if files == nil {
		files = make([]map[string]any, 0)
	}

	sendJSON(w, http.StatusOK, map[string]any{
		"git_installed": true, "is_repo": true, "is_temp": isTemp, "files": files,
	})
}

func (s *Server) gitInit(w http.ResponseWriter, _ *http.Request) {
	chapDir := s.state.ChaptersDir()
	root := chapDir
	if root != "" && filepath.Base(root) == "chapters" {
		root = filepath.Dir(root)
	}
	rc, _, errStr := git.RunGit(root, "init")
	if rc != 0 {
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": strings.TrimSpace(errStr)})
		return
	}
	sendJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) gitStage(w http.ResponseWriter, r *http.Request) {
	chapDir := s.state.ChaptersDir()
	body, err := readBody(r)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid request body"})
		return
	}

	var rc int
	var errStr string

	if all, _ := body["all"].(bool); all {
		scope := s.gitScope()
		rc, _, errStr = git.RunGit(chapDir, "add", "--", scope)
	} else {
		path, _ := body["path"].(string)
		if path == "" {
			sendJSON(w, http.StatusBadRequest, map[string]any{"error": "No path specified"})
			return
		}
		rc, _, errStr = git.RunGit(chapDir, "add", "--", git.ResolvePath(chapDir, path))
	}

	if rc != 0 {
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": strings.TrimSpace(errStr)})
		return
	}
	sendJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) gitUnstage(w http.ResponseWriter, r *http.Request) {
	chapDir := s.state.ChaptersDir()
	body, err := readBody(r)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid request body"})
		return
	}

	hasCommits := git.HasCommits(chapDir)

	var rc int
	var errStr string

	if all, _ := body["all"].(bool); all {
		scope := s.gitScope()
		if hasCommits {
			rc, _, errStr = git.RunGit(chapDir, "reset", "HEAD", "--", scope)
		} else {
			rc, _, errStr = git.RunGit(chapDir, "rm", "--cached", "-r", "--", scope)
		}
	} else {
		path, _ := body["path"].(string)
		if path == "" {
			sendJSON(w, http.StatusBadRequest, map[string]any{"error": "No path specified"})
			return
		}
		fullPath := git.ResolvePath(chapDir, path)
		if hasCommits {
			rc, _, errStr = git.RunGit(chapDir, "reset", "HEAD", "--", fullPath)
		} else {
			rc, _, errStr = git.RunGit(chapDir, "rm", "--cached", "--", fullPath)
		}
	}

	if rc != 0 {
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": strings.TrimSpace(errStr)})
		return
	}
	sendJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) gitCommit(w http.ResponseWriter, r *http.Request) {
	chapDir := s.state.ChaptersDir()
	body, err := readBody(r)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid request body"})
		return
	}

	msg, _ := body["message"].(string)
	msg = strings.TrimSpace(msg)
	if msg == "" {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Commit message required"})
		return
	}

	rc, out, errStr := git.RunGit(chapDir, "commit", "-m", msg)
	if rc != 0 {
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": strings.TrimSpace(errStr)})
		return
	}

	sha := ""
	if m := shaRe.FindStringSubmatch(out); len(m) >= 2 {
		sha = m[1]
	}

	sendJSON(w, http.StatusOK, map[string]any{"ok": true, "sha": sha})
}

func (s *Server) gitLog(w http.ResponseWriter, _ *http.Request) {
	chapDir := s.state.ChaptersDir()
	if !git.Installed() || git.Root(chapDir) == "" {
		sendJSON(w, http.StatusOK, map[string]any{"commits": []any{}})
		return
	}

	scope := s.gitScope()
	rc, out, _ := git.RunGit(chapDir, "log", "--format=%H\t%h\t%s\t%ar", "-20", "--", scope)
	if rc != 0 {
		sendJSON(w, http.StatusOK, map[string]any{"commits": []any{}})
		return
	}

	var commits []map[string]string
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		parts := strings.SplitN(line, "\t", 4)
		if len(parts) == 4 {
			commits = append(commits, map[string]string{
				"sha":     parts[0],
				"short":   parts[1],
				"message": parts[2],
				"when":    parts[3],
			})
		}
	}
	if commits == nil {
		commits = make([]map[string]string, 0)
	}

	sendJSON(w, http.StatusOK, map[string]any{"commits": commits})
}

func (s *Server) gitRestore(w http.ResponseWriter, r *http.Request) {
	chapDir := s.state.ChaptersDir()
	body, err := readBody(r)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid request body"})
		return
	}

	sha, _ := body["sha"].(string)
	sha = strings.TrimSpace(sha)
	if sha == "" {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "No commit specified"})
		return
	}
	if !git.HasCommits(chapDir) {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "No commits to restore from"})
		return
	}

	scope := s.gitScope()

	// Checkout all project files from that commit.
	rc, _, errStr := git.RunGit(chapDir, "checkout", sha, "--", scope)
	if rc != 0 {
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": strings.TrimSpace(errStr)})
		return
	}

	// Find the original commit message.
	rc2, msgOut, _ := git.RunGit(chapDir, "log", "--format=%s", "-1", sha)
	origMsg := sha[:7]
	if rc2 == 0 {
		origMsg = strings.TrimSpace(msgOut)
	}

	// Stage and auto-commit.
	git.RunGit(chapDir, "add", "--", scope) //nolint:errcheck
	rc3, out3, err3 := git.RunGit(chapDir, "commit", "-m", "Restored to: "+origMsg)
	if rc3 != 0 {
		if strings.Contains(err3, "nothing to commit") || strings.Contains(out3, "nothing to commit") {
			sendJSON(w, http.StatusOK, map[string]any{"ok": true, "message": "Already at that version"})
			return
		}
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": strings.TrimSpace(err3)})
		return
	}

	sendJSON(w, http.StatusOK, map[string]any{"ok": true, "message": "Restored to: " + origMsg})
}
