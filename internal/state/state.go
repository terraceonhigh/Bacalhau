// Package state provides mutex-protected shared application state.
package state

import (
	"sync"
	"time"
)

// AppState holds the global mutable state for the application.
type AppState struct {
	mu            sync.RWMutex
	chaptersDir   string
	bacalhauFile  string
	bacalhauName  string
	tempDir       string
	lastHeartbeat time.Time
}

// New returns an AppState with the heartbeat initialised to now.
func New() *AppState {
	return &AppState{
		lastHeartbeat: time.Now(),
	}
}

// ChaptersDir returns the current chapters directory.
func (s *AppState) ChaptersDir() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.chaptersDir
}

// SetChaptersDir sets the chapters directory.
func (s *AppState) SetChaptersDir(v string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.chaptersDir = v
}

// BacalhauFile returns the path to the open .bacalhau file, or "".
func (s *AppState) BacalhauFile() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.bacalhauFile
}

// SetBacalhauFile sets the .bacalhau file path.
func (s *AppState) SetBacalhauFile(v string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.bacalhauFile = v
}

// BacalhauName returns the original filename of the opened project.
func (s *AppState) BacalhauName() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.bacalhauName
}

// SetBacalhauName sets the original filename.
func (s *AppState) SetBacalhauName(v string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.bacalhauName = v
}

// TempDir returns the temporary extraction directory, or "".
func (s *AppState) TempDir() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.tempDir
}

// SetTempDir sets the temporary directory.
func (s *AppState) SetTempDir(v string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tempDir = v
}

// UpdateHeartbeat records the current time as the last heartbeat.
func (s *AppState) UpdateHeartbeat() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.lastHeartbeat = time.Now()
}

// HeartbeatAge returns the duration since the last heartbeat.
func (s *AppState) HeartbeatAge() time.Duration {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return time.Since(s.lastHeartbeat)
}
