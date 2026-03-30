// Package server provides the HTTP handler for the Bacalhau editor.
package server

import (
	"io/fs"
	"net/http"
	"sync"

	"github.com/terraceonhigh/Bacalhau/internal/state"
)

// Server holds the shared state and embedded filesystems for the HTTP server.
type Server struct {
	state    *state.AppState
	staticFS fs.FS    // embedded static/
	vendorFS fs.FS    // embedded vendor_js/ (served at /vendor/)
	themesFS fs.FS    // embedded themes/
	iconPNG  []byte   // embedded icon
	fsMu     sync.Mutex

	version    string
	// shutdownFn is called when the /api/shutdown endpoint fires.
	shutdownFn func()
	// repackFn is called before switching projects or shutting down.
	repackFn func()
}

// New creates a Server and wires all routes. shutdownFn is called after the
// shutdown endpoint responds. repackFn is called when the current .bacalhau
// project should be saved before switching/closing.
func New(
	appState *state.AppState,
	staticFS fs.FS,
	vendorFS fs.FS,
	themesFS fs.FS,
	iconPNG []byte,
	version string,
	shutdownFn func(),
	repackFn func(),
) *Server {
	return &Server{
		state:      appState,
		version:    version,
		staticFS:   staticFS,
		vendorFS:   vendorFS,
		themesFS:   themesFS,
		iconPNG:    iconPNG,
		shutdownFn: shutdownFn,
		repackFn:   repackFn,
	}
}

// Handler returns the http.Handler with all routes wired.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()

	// Static assets
	mux.HandleFunc("GET /", s.serveHTML)
	mux.HandleFunc("GET /favicon.png", s.serveFavicon)
	mux.HandleFunc("GET /static/{path...}", s.serveStatic)
	mux.HandleFunc("GET /vendor/{path...}", s.serveVendor)

	// Tree & preview
	mux.HandleFunc("GET /api/tree", s.getTree)
	mux.HandleFunc("GET /api/preview", s.getPreview)

	// Chapter CRUD — PUT and DELETE use {path...}
	mux.HandleFunc("GET /api/chapter/{path...}", s.getChapter)
	mux.HandleFunc("PUT /api/chapter/{path...}", s.putChapter)
	mux.HandleFunc("DELETE /api/chapter/{path...}", s.deleteChapter)

	// File operations
	mux.HandleFunc("POST /api/chapter/new", s.newChapter)
	mux.HandleFunc("POST /api/dir/new", s.newDir)
	mux.HandleFunc("POST /api/tree/move", s.moveItem)
	mux.HandleFunc("POST /api/rename", s.renameItem)

	// Copy and chmod — Go 1.22 ServeMux doesn't support suffix matching,
	// so we use a catch-all POST handler for /api/chapter/ and /api/dir/ paths
	// that checks suffixes.
	mux.HandleFunc("POST /api/chapter/{path...}", s.postChapterDispatch)
	mux.HandleFunc("POST /api/dir/{path...}", s.postDirDispatch)
	mux.HandleFunc("DELETE /api/dir/{path...}", s.deleteDir)

	// Export
	mux.HandleFunc("GET /api/export/markdown", s.exportMarkdown)
	mux.HandleFunc("GET /api/export/html", s.exportHTML)
	mux.HandleFunc("GET /api/save/zip", s.saveZip)
	mux.HandleFunc("GET /api/save/bacalhau", s.saveBacalhau)

	// Project opening
	mux.HandleFunc("POST /api/open", s.openProject)
	mux.HandleFunc("POST /api/open/folder", s.openFolder)
	mux.HandleFunc("GET /api/browse", s.browseDirectory)

	// Git
	mux.HandleFunc("GET /api/git/status", s.gitStatus)
	mux.HandleFunc("GET /api/git/log", s.gitLog)
	mux.HandleFunc("POST /api/git/init", s.gitInit)
	mux.HandleFunc("POST /api/git/stage", s.gitStage)
	mux.HandleFunc("POST /api/git/unstage", s.gitUnstage)
	mux.HandleFunc("POST /api/git/commit", s.gitCommit)
	mux.HandleFunc("POST /api/git/restore", s.gitRestore)

	// Themes
	mux.HandleFunc("GET /api/themes", s.getThemes)
	mux.HandleFunc("POST /api/themes/import", s.importTheme)
	mux.HandleFunc("GET /api/themes/{name...}", s.getThemeCSS)

	// Lifecycle
	mux.HandleFunc("GET /api/version", s.getVersion)
	mux.HandleFunc("GET /api/heartbeat", s.heartbeat)
	mux.HandleFunc("POST /api/shutdown", s.shutdown)

	return mux
}

// postChapterDispatch routes POST /api/chapter/{path...} to copy or chmod
// based on the URL suffix.
func (s *Server) postChapterDispatch(w http.ResponseWriter, r *http.Request) {
	p := r.PathValue("path")
	switch {
	case len(p) > 5 && p[len(p)-5:] == "/copy":
		s.copyChapter(w, r)
	case len(p) > 6 && p[len(p)-6:] == "/chmod":
		s.chmodChapter(w, r)
	default:
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
	}
}

// postDirDispatch routes POST /api/dir/{path...} to copyDir.
func (s *Server) postDirDispatch(w http.ResponseWriter, r *http.Request) {
	p := r.PathValue("path")
	if len(p) > 5 && p[len(p)-5:] == "/copy" {
		s.copyDir(w, r)
	} else {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
	}
}
