package server

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"

	bfs "github.com/terraceonhigh/Bacalhau/internal/fs"
)

func (s *Server) getChapter(w http.ResponseWriter, r *http.Request) {
	relpath := r.PathValue("path")

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	abspath, err := s.resolvePath(relpath)
	if err != nil {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": err.Error()})
		return
	}
	content, err := os.ReadFile(abspath)
	if err != nil {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": err.Error()})
		return
	}
	sendJSON(w, http.StatusOK, map[string]any{"content": string(content)})
}

func (s *Server) putChapter(w http.ResponseWriter, r *http.Request) {
	relpath := r.PathValue("path")

	body, err := readBody(r)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid request body"})
		return
	}

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	abspath, err := s.resolvePath(relpath)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}

	// Check read-only.
	if f, err := os.OpenFile(abspath, os.O_WRONLY, 0); err != nil {
		if os.IsPermission(err) {
			sendJSON(w, http.StatusForbidden, map[string]any{"error": "File is read-only"})
			return
		}
	} else {
		f.Close()
	}

	content, _ := body["content"].(string)
	if err := os.WriteFile(abspath, []byte(content), 0o644); err != nil {
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	sendJSON(w, http.StatusOK, map[string]any{"message": "Saved"})
}

func (s *Server) deleteChapter(w http.ResponseWriter, r *http.Request) {
	relpath := r.PathValue("path")

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	abspath, err := s.resolvePath(relpath)
	if err != nil {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": err.Error()})
		return
	}
	if _, statErr := os.Stat(abspath); statErr != nil {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
		return
	}

	dirname := filepath.Dir(abspath)
	fname := filepath.Base(abspath)
	os.Remove(abspath) //nolint:errcheck

	order := bfs.ReadOrder(dirname)
	filtered := make([]string, 0, len(order))
	for _, e := range order {
		if e != fname {
			filtered = append(filtered, e)
		}
	}
	bfs.WriteOrder(dirname, filtered) //nolint:errcheck

	sendJSON(w, http.StatusOK, map[string]any{"message": "Deleted " + relpath})
}

func (s *Server) copyChapter(w http.ResponseWriter, r *http.Request) {
	p := r.PathValue("path")
	// Strip trailing "/copy"
	relpath := strings.TrimSuffix(p, "/copy")

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	abspath, err := s.resolvePath(relpath)
	if err != nil || !fileExists(abspath) {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
		return
	}

	dirname := filepath.Dir(abspath)
	basename := strings.TrimSuffix(filepath.Base(abspath), ".md")
	n := 1
	var newName string
	for {
		suffix := "-copy"
		if n > 1 {
			suffix = "-copy-" + itoa(n)
		}
		newName = basename + suffix + ".md"
		if !fileExists(filepath.Join(dirname, newName)) {
			break
		}
		n++
	}

	// Update _order.yaml BEFORE copying on disk.
	order := bfs.ReadOrderRaw(dirname)
	origName := filepath.Base(abspath)
	idx := len(order)
	for i, e := range order {
		if e == origName {
			idx = i + 1
			break
		}
	}
	order = insertAt(order, idx, newName)
	bfs.WriteOrder(dirname, order) //nolint:errcheck

	src, _ := os.ReadFile(abspath)
	os.WriteFile(filepath.Join(dirname, newName), src, 0o644) //nolint:errcheck

	sendJSON(w, http.StatusOK, map[string]any{"message": "Copied to " + newName})
}

func (s *Server) chmodChapter(w http.ResponseWriter, r *http.Request) {
	p := r.PathValue("path")
	relpath := strings.TrimSuffix(p, "/chmod")

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	abspath, err := s.resolvePath(relpath)
	if err != nil || !fileExists(abspath) {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
		return
	}

	info, err := os.Stat(abspath)
	if err != nil {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
		return
	}

	mode := info.Mode()
	if mode&0o200 != 0 {
		// Currently writable -> make read-only.
		os.Chmod(abspath, mode&^0o200) //nolint:errcheck
		sendJSON(w, http.StatusOK, map[string]any{
			"writable": false,
			"message":  relpath + " \u2192 read-only",
		})
	} else {
		// Currently read-only -> make writable.
		os.Chmod(abspath, mode|0o200) //nolint:errcheck
		sendJSON(w, http.StatusOK, map[string]any{
			"writable": true,
			"message":  relpath + " \u2192 writable",
		})
	}
}

// --- small helpers ---

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	s := ""
	neg := n < 0
	if neg {
		n = -n
	}
	for n > 0 {
		s = string(rune('0'+n%10)) + s
		n /= 10
	}
	if neg {
		s = "-" + s
	}
	return s
}

func insertAt(slice []string, index int, value string) []string {
	if index >= len(slice) {
		return append(slice, value)
	}
	slice = append(slice, "")
	copy(slice[index+1:], slice[index:])
	slice[index] = value
	return slice
}
