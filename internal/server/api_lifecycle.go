package server

import (
	"net/http"
	"time"
)

func (s *Server) getVersion(w http.ResponseWriter, _ *http.Request) {
	sendJSON(w, http.StatusOK, map[string]any{"version": s.version})
}

func (s *Server) heartbeat(w http.ResponseWriter, _ *http.Request) {
	s.state.UpdateHeartbeat()
	sendJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) shutdown(w http.ResponseWriter, _ *http.Request) {
	if s.repackFn != nil {
		s.repackFn()
	}
	sendJSON(w, http.StatusOK, map[string]any{"ok": true})

	// Trigger shutdown after giving time for the response to be sent.
	if s.shutdownFn != nil {
		go func() {
			time.Sleep(500 * time.Millisecond)
			s.shutdownFn()
		}()
	}
}
