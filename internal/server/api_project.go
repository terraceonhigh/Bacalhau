package server

import (
	"encoding/base64"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"

	bfs "github.com/terraceonhigh/Bacalhau/internal/fs"
)

func (s *Server) openProject(w http.ResponseWriter, r *http.Request) {
	body, err := readBody(r)
	if err != nil || body["data"] == nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Missing file data"})
		return
	}

	dataStr, _ := body["data"].(string)
	raw, err := base64.StdEncoding.DecodeString(dataStr)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid file data"})
		return
	}

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	// Save current project if it's a .bacalhau.
	if s.repackFn != nil {
		s.repackFn()
	}

	oldTemp := s.state.TempDir()

	// Extract to new temp dir.
	newTemp, err := os.MkdirTemp("", "bacalhau-")
	if err != nil {
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}

	tmpFile := filepath.Join(newTemp, "upload.bacalhau")
	if err := os.WriteFile(tmpFile, raw, 0o644); err != nil {
		os.RemoveAll(newTemp)
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}

	if err := bfs.Extract(tmpFile, newTemp); err != nil {
		os.RemoveAll(newTemp)
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Not a valid .bacalhau file"})
		return
	}
	os.Remove(tmpFile) //nolint:errcheck

	chaptersPath := filepath.Join(newTemp, "chapters")
	if info, err := os.Stat(chaptersPath); err != nil || !info.IsDir() {
		os.RemoveAll(newTemp)
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "No chapters/ directory in file"})
		return
	}

	// Switch to new project.
	s.state.SetChaptersDir(chaptersPath)
	s.state.SetBacalhauFile("") // Uploaded copy - no disk path.
	filename, _ := body["filename"].(string)
	if filename == "" {
		filename = "project.bacalhau"
	}
	s.state.SetBacalhauName(filename)
	s.state.SetTempDir(newTemp)

	// Clean up old temp.
	if oldTemp != "" {
		os.RemoveAll(oldTemp) //nolint:errcheck
	}

	sendJSON(w, http.StatusOK, map[string]any{"ok": true, "name": filename})
}

func (s *Server) openFolder(w http.ResponseWriter, r *http.Request) {
	home, _ := os.UserHomeDir()
	body, err := readBody(r)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid request body"})
		return
	}

	reqPath, _ := body["path"].(string)
	reqPath = strings.TrimSpace(reqPath)
	if reqPath == "" {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "No path specified"})
		return
	}

	target, err := filepath.EvalSymlinks(reqPath)
	if err != nil {
		target = filepath.Clean(reqPath)
	}

	if !strings.HasPrefix(target, home) {
		sendJSON(w, http.StatusForbidden, map[string]any{"error": "Access denied"})
		return
	}
	if info, err := os.Stat(target); err != nil || !info.IsDir() {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Directory not found"})
		return
	}

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	if s.repackFn != nil {
		s.repackFn()
	}
	oldTemp := s.state.TempDir()

	s.state.SetChaptersDir(target)
	s.state.SetBacalhauFile("")
	s.state.SetBacalhauName("")
	s.state.SetTempDir("")

	if oldTemp != "" {
		os.RemoveAll(oldTemp) //nolint:errcheck
	}

	sendJSON(w, http.StatusOK, map[string]any{"ok": true, "path": target})
}

func (s *Server) browseDirectory(w http.ResponseWriter, r *http.Request) {
	home, _ := os.UserHomeDir()

	qs := r.URL.Query()
	reqPath := qs.Get("path")
	if reqPath != "" {
		var err error
		reqPath, err = url.PathUnescape(reqPath)
		if err != nil {
			sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid path"})
			return
		}
	}

	target := home
	if reqPath != "" {
		resolved, err := filepath.EvalSymlinks(reqPath)
		if err != nil {
			resolved = filepath.Clean(reqPath)
		}
		target = resolved
	}

	// Security: restrict to home directory.
	if !strings.HasPrefix(target, home) {
		sendJSON(w, http.StatusForbidden, map[string]any{"error": "Access denied"})
		return
	}
	if info, err := os.Stat(target); err != nil || !info.IsDir() {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Directory not found"})
		return
	}

	rawEntries, err := os.ReadDir(target)
	if err != nil {
		sendJSON(w, http.StatusForbidden, map[string]any{"error": "Permission denied"})
		return
	}

	// Sort case-insensitively.
	sort.Slice(rawEntries, func(i, j int) bool {
		return strings.ToLower(rawEntries[i].Name()) < strings.ToLower(rawEntries[j].Name())
	})

	var dirs []map[string]any
	for _, de := range rawEntries {
		name := de.Name()
		if strings.HasPrefix(name, ".") || !de.IsDir() {
			continue
		}
		full := filepath.Join(target, name)
		children, err := os.ReadDir(full)
		if err != nil {
			children = nil
		}
		mdCount := 0
		hasOrder := false
		for _, c := range children {
			if strings.HasSuffix(c.Name(), ".md") {
				mdCount++
			}
			if c.Name() == "_order.yaml" {
				hasOrder = true
			}
		}
		isProject := hasOrder || mdCount > 0
		dirs = append(dirs, map[string]any{
			"name":      name,
			"isProject": isProject,
			"mdCount":   mdCount,
		})
		if len(dirs) >= 200 {
			break
		}
	}
	if dirs == nil {
		dirs = make([]map[string]any, 0)
	}

	// Current directory info.
	curChildren, _ := os.ReadDir(target)
	curMD := 0
	curHasOrder := false
	for _, c := range curChildren {
		if strings.HasSuffix(c.Name(), ".md") {
			curMD++
		}
		if c.Name() == "_order.yaml" {
			curHasOrder = true
		}
	}
	curIsProject := curHasOrder || curMD > 0

	parent := filepath.Dir(target)
	var parentPtr any = parent
	if !strings.HasPrefix(parent, home) {
		parentPtr = nil
	}

	sendJSON(w, http.StatusOK, map[string]any{
		"path":      target,
		"home":      home,
		"parent":    parentPtr,
		"atHome":    target == home,
		"isProject": curIsProject,
		"mdCount":   curMD,
		"entries":   dirs,
	})
}

// Ensure we use the fs package alias to avoid unused import.
var _ = fmt.Sprint
