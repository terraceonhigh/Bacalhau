// Package themes provides helpers for discovering and reading CSS themes.
package themes

import (
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
)

// BundledDir returns the themes/ directory next to the running executable,
// or "" if it does not exist.
func BundledDir() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	d := filepath.Join(filepath.Dir(exe), "themes")
	if info, err := os.Stat(d); err == nil && info.IsDir() {
		return d
	}
	return ""
}

// UserDir returns the platform-appropriate user themes directory, creating it
// if it does not already exist.
func UserDir() string {
	var base string
	if runtime.GOOS == "darwin" {
		home, _ := os.UserHomeDir()
		base = filepath.Join(home, "Library", "Application Support", "Bacalhau")
	} else {
		xdg := os.Getenv("XDG_DATA_HOME")
		if xdg == "" {
			home, _ := os.UserHomeDir()
			xdg = filepath.Join(home, ".local", "share")
		}
		base = filepath.Join(xdg, "Bacalhau")
	}
	d := filepath.Join(base, "themes")
	_ = os.MkdirAll(d, 0o755)
	return d
}

// List returns sorted theme CSS filenames. User themes are checked first so
// they can override bundled themes with the same name. bundledFS is an
// embed.FS from the main package containing the bundled themes/ directory.
func List(bundledFS fs.FS) []string {
	seen := make(map[string]bool)
	var themes []string

	// User themes take priority (listed first so they override bundled).
	userDir := UserDir()
	if entries, err := os.ReadDir(userDir); err == nil {
		for _, e := range entries {
			name := e.Name()
			if strings.HasSuffix(name, ".css") && !seen[name] {
				seen[name] = true
				themes = append(themes, name)
			}
		}
	}

	// Bundled themes from the embedded filesystem.
	if bundledFS != nil {
		if entries, err := fs.ReadDir(bundledFS, "themes"); err == nil {
			for _, e := range entries {
				name := e.Name()
				if strings.HasSuffix(name, ".css") && !seen[name] {
					seen[name] = true
					themes = append(themes, name)
				}
			}
		}
	}

	sort.Strings(themes)
	return themes
}

// Find locates a theme CSS file by name, checking the user directory first,
// then the bundled embed.FS. Returns the CSS content or an error.
func Find(name string, bundledFS fs.FS) ([]byte, error) {
	// Check user dir first.
	userPath := filepath.Join(UserDir(), name)
	if data, err := os.ReadFile(userPath); err == nil {
		return data, nil
	}

	// Check bundled dir on disk (next to executable).
	if bd := BundledDir(); bd != "" {
		diskPath := filepath.Join(bd, name)
		if data, err := os.ReadFile(diskPath); err == nil {
			return data, nil
		}
	}

	// Check embedded filesystem.
	if bundledFS != nil {
		if data, err := fs.ReadFile(bundledFS, filepath.Join("themes", name)); err == nil {
			return data, nil
		}
	}

	return nil, os.ErrNotExist
}

// Import saves theme CSS data to the user themes directory.
func Import(name string, data []byte) error {
	d := UserDir()
	return os.WriteFile(filepath.Join(d, name), data, 0o644)
}
