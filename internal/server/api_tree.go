package server

import (
	"fmt"
	iofs "io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	bfs "github.com/terraceonhigh/Bacalhau/internal/fs"
)

func (s *Server) serveHTML(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
		return
	}
	data, err := iofs.ReadFile(s.staticFS, "static/index.html")
	if err != nil {
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": "index.html not found"})
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Content-Length", fmt.Sprint(len(data)))
	w.Write(data) //nolint:errcheck
}

func (s *Server) serveFavicon(w http.ResponseWriter, _ *http.Request) {
	if len(s.iconPNG) == 0 {
		w.WriteHeader(http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "image/png")
	w.Header().Set("Content-Length", fmt.Sprint(len(s.iconPNG)))
	w.Header().Set("Cache-Control", "max-age=86400")
	w.Write(s.iconPNG) //nolint:errcheck
}

func (s *Server) serveStatic(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("path")
	if strings.Contains(name, "..") || strings.HasPrefix(name, "/") {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid path"})
		return
	}
	data, err := iofs.ReadFile(s.staticFS, "static/"+name)
	if err != nil {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
		return
	}
	ct := mimeForExt(filepath.Ext(name))
	w.Header().Set("Content-Type", ct)
	w.Header().Set("Content-Length", fmt.Sprint(len(data)))
	w.Header().Set("Cache-Control", "max-age=86400")
	w.Write(data) //nolint:errcheck
}

func (s *Server) serveVendor(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("path")
	if strings.Contains(name, "/") || strings.HasPrefix(name, ".") {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid path"})
		return
	}
	data, err := iofs.ReadFile(s.vendorFS, "vendor_js/"+name)
	if err != nil {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
		return
	}
	ct := "application/octet-stream"
	if strings.HasSuffix(name, ".js") {
		ct = "application/javascript"
	}
	w.Header().Set("Content-Type", ct)
	w.Header().Set("Content-Length", fmt.Sprint(len(data)))
	w.Header().Set("Cache-Control", "max-age=86400")
	w.Write(data) //nolint:errcheck
}

func (s *Server) getTree(w http.ResponseWriter, _ *http.Request) {
	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	chapDir := s.state.ChaptersDir()
	tree := bfs.BuildTree(chapDir)

	// Derive project name: BACALHAU_NAME > basename of BACALHAU_FILE > parent of chapters dir.
	var pname string
	if n := s.state.BacalhauName(); n != "" {
		pname = strings.TrimSuffix(n, ".bacalhau")
	} else if f := s.state.BacalhauFile(); f != "" {
		pname = strings.TrimSuffix(filepath.Base(f), ".bacalhau")
	} else {
		d := chapDir
		if d != "" && filepath.Base(d) == "chapters" {
			d = filepath.Dir(d)
		}
		if d != "" {
			pname = filepath.Base(d)
		}
	}

	sendJSON(w, http.StatusOK, map[string]any{"tree": tree, "project": pname})
}

func (s *Server) getPreview(w http.ResponseWriter, _ *http.Request) {
	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	chapDir := s.state.ChaptersDir()
	var files []map[string]string
	for _, fpath := range bfs.WalkFiles(chapDir) {
		rel, _ := filepath.Rel(chapDir, fpath)
		content, err := os.ReadFile(fpath)
		if err != nil {
			content = nil
		}
		files = append(files, map[string]string{
			"path":    rel,
			"content": string(content),
		})
	}
	if files == nil {
		files = make([]map[string]string, 0)
	}
	sendJSON(w, http.StatusOK, map[string]any{"files": files})
}

// mimeForExt returns a Content-Type for a file extension.
func mimeForExt(ext string) string {
	switch ext {
	case ".html":
		return "text/html; charset=utf-8"
	case ".css":
		return "text/css; charset=utf-8"
	case ".js":
		return "application/javascript"
	default:
		return "application/octet-stream"
	}
}
