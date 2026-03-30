package server

import (
	"encoding/base64"
	"net/http"
	"strings"

	"github.com/terraceonhigh/Bacalhau/internal/themes"
)

func (s *Server) getThemes(w http.ResponseWriter, _ *http.Request) {
	list := themes.List(s.themesFS)
	if list == nil {
		list = []string{}
	}
	sendJSON(w, http.StatusOK, map[string]any{"themes": list})
}

func (s *Server) getThemeCSS(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	// Prevent path traversal.
	if strings.Contains(name, "/") || strings.HasPrefix(name, ".") {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid theme name"})
		return
	}
	if !strings.HasSuffix(name, ".css") {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Theme not found"})
		return
	}

	data, err := themes.Find(name, s.themesFS)
	if err != nil {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Theme not found"})
		return
	}

	w.Header().Set("Content-Type", "text/css; charset=utf-8")
	w.Write(data) //nolint:errcheck
}

func (s *Server) importTheme(w http.ResponseWriter, r *http.Request) {
	body, err := readBody(r)
	if err != nil || body["data"] == nil || body["filename"] == nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Missing file data"})
		return
	}

	name, _ := body["filename"].(string)
	if !strings.HasSuffix(name, ".css") || strings.Contains(name, "/") || strings.HasPrefix(name, ".") {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid theme filename"})
		return
	}

	dataStr, _ := body["data"].(string)
	raw, err := base64.StdEncoding.DecodeString(dataStr)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid file data"})
		return
	}

	if err := themes.Import(name, raw); err != nil {
		sendJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}

	sendJSON(w, http.StatusOK, map[string]any{"ok": true, "name": name})
}
