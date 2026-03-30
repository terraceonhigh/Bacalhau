package server

import (
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

// sendJSON writes a JSON response with the given status code.
func sendJSON(w http.ResponseWriter, code int, data any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(data) //nolint:errcheck
}

// readBody reads and JSON-decodes the request body into a map.
func readBody(r *http.Request) (map[string]any, error) {
	defer r.Body.Close()
	body, err := io.ReadAll(r.Body)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(body, &m); err != nil {
		return nil, err
	}
	return m, nil
}

// resolvePath URL-decodes relpath, joins it with ChaptersDir, and validates
// that the result does not escape the chapters directory.
func (s *Server) resolvePath(relpath string) (string, error) {
	decoded, err := url.PathUnescape(relpath)
	if err != nil {
		return "", err
	}

	chapDir := s.state.ChaptersDir()
	if chapDir == "" {
		return "", os.ErrNotExist
	}

	// Clean and join.
	joined := filepath.Join(chapDir, filepath.FromSlash(decoded))
	abs, err := filepath.Abs(joined)
	if err != nil {
		return "", err
	}

	// Evaluate symlinks on the chapters dir for comparison.
	realChap, err := filepath.EvalSymlinks(chapDir)
	if err != nil {
		realChap = chapDir
	}

	// Evaluate symlinks on the resolved path.
	realAbs := abs
	if _, statErr := os.Stat(abs); statErr == nil {
		if resolved, evalErr := filepath.EvalSymlinks(abs); evalErr == nil {
			realAbs = resolved
		}
	}

	if !strings.HasPrefix(realAbs, realChap+string(os.PathSeparator)) && realAbs != realChap {
		return "", os.ErrPermission
	}

	return abs, nil
}
