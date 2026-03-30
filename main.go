package main

import (
	"context"
	"embed"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	bfs "github.com/terraceonhigh/Bacalhau/internal/fs"
	"github.com/terraceonhigh/Bacalhau/internal/server"
	"github.com/terraceonhigh/Bacalhau/internal/state"

	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
	wailsRuntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

//go:embed static/*
var staticFS embed.FS

//go:embed vendor_js/*
var vendorFS embed.FS

//go:embed themes/*.css
var themesFS embed.FS

//go:embed icons/icon.png
var iconPNG []byte

var version = "dev"

// app bridges the Wails lifecycle with the Bacalhau server.
type app struct {
	ctx      context.Context
	repackFn func()
	tempDirs []string
}

func (a *app) startup(ctx context.Context) {
	a.ctx = ctx
	wailsRuntime.WindowShow(ctx)
}

func (a *app) shutdown(ctx context.Context) {
	a.repackFn()
	for _, d := range a.tempDirs {
		os.RemoveAll(d) //nolint:errcheck
	}
}

// FileResult is returned by OpenFile to the frontend.
type FileResult struct {
	Filename string `json:"filename"`
	Data     string `json:"data"` // base64-encoded
}

// OpenFile opens a native file dialog for .bacalhau files and returns the
// filename + base64-encoded contents. Returns empty result if cancelled.
func (a *app) OpenFile() (*FileResult, error) {
	path, err := wailsRuntime.OpenFileDialog(a.ctx, wailsRuntime.OpenDialogOptions{
		Title: "Open Project",
		Filters: []wailsRuntime.FileFilter{
			{DisplayName: "Bacalhau Projects", Pattern: "*.bacalhau"},
		},
	})
	if err != nil {
		return nil, err
	}
	if path == "" {
		return nil, nil // cancelled
	}

	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}

	return &FileResult{
		Filename: filepath.Base(path),
		Data:     base64.StdEncoding.EncodeToString(data),
	}, nil
}

// OpenFolder opens a native directory picker and returns the chosen path.
// Returns empty string if cancelled.
func (a *app) OpenFolder() (string, error) {
	path, err := wailsRuntime.OpenDirectoryDialog(a.ctx, wailsRuntime.OpenDialogOptions{
		Title: "Open Folder",
	})
	if err != nil {
		return "", err
	}
	return path, nil
}

// SaveToFile opens a native save dialog and writes base64-encoded data to the
// chosen path. filterDesc and filterPattern control the file type filter
// (e.g. "Bacalhau Projects", "*.bacalhau"). Returns the path, or empty string
// if cancelled.
func (a *app) SaveToFile(suggestedName, filterDesc, filterPattern, b64data string) (string, error) {
	path, err := wailsRuntime.SaveFileDialog(a.ctx, wailsRuntime.SaveDialogOptions{
		Title:           "Save",
		DefaultFilename: suggestedName,
		Filters: []wailsRuntime.FileFilter{
			{DisplayName: filterDesc, Pattern: filterPattern},
		},
	})
	if err != nil {
		return "", err
	}
	if path == "" {
		return "", nil // cancelled
	}

	data, err := base64.StdEncoding.DecodeString(b64data)
	if err != nil {
		return "", err
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return "", err
	}
	return path, nil
}

func main() {
	// Parse args: bacalhau [project-path]
	args := os.Args[1:]
	var projectDir string

	for i := 0; i < len(args); i++ {
		if !strings.HasPrefix(args[i], "-") {
			projectDir = args[i]
		}
	}

	appState := state.New()
	a := &app{}

	// --- Project setup (unchanged) ---

	if projectDir == "" {
		tmpDir, err := os.MkdirTemp("", "bacalhau-empty-")
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
		a.tempDirs = append(a.tempDirs, tmpDir)
		chapDir := filepath.Join(tmpDir, "chapters")
		os.MkdirAll(chapDir, 0o755) //nolint:errcheck
		appState.SetTempDir(tmpDir)
		appState.SetChaptersDir(chapDir)
	} else {
		projectDir, _ = filepath.Abs(projectDir)

		if strings.HasSuffix(projectDir, ".bacalhau") && isFile(projectDir) {
			appState.SetBacalhauFile(projectDir)

			tmpDir, err := os.MkdirTemp("", "bacalhau-")
			if err != nil {
				fmt.Fprintf(os.Stderr, "Error: %v\n", err)
				os.Exit(1)
			}
			a.tempDirs = append(a.tempDirs, tmpDir)

			if err := bfs.Extract(projectDir, tmpDir); err != nil {
				fmt.Fprintf(os.Stderr, "Error: %v\n", err)
				os.RemoveAll(tmpDir)
				os.Exit(1)
			}

			chapDir := filepath.Join(tmpDir, "chapters")
			if info, err := os.Stat(chapDir); err != nil || !info.IsDir() {
				fmt.Fprintf(os.Stderr, "Error: no chapters/ directory in %s\n", projectDir)
				os.RemoveAll(tmpDir)
				os.Exit(1)
			}
			appState.SetTempDir(tmpDir)
			appState.SetChaptersDir(chapDir)
			fmt.Fprintf(os.Stderr, "Opened: %s -> %s\n", projectDir, tmpDir)
		} else {
			if info, err := os.Stat(projectDir); err != nil || !info.IsDir() {
				fmt.Fprintf(os.Stderr, "Error: not a directory: %s\n", projectDir)
				os.Exit(1)
			}
			appState.SetChaptersDir(projectDir)
		}
	}

	// --- Repack & shutdown ---

	a.repackFn = func() {
		bf := appState.BacalhauFile()
		cd := appState.ChaptersDir()
		if bf != "" && cd != "" {
			if err := bfs.Repack(cd, bf); err != nil {
				fmt.Fprintf(os.Stderr, "Repack error: %v\n", err)
			}
		}
	}

	// shutdownFn for the /api/shutdown endpoint — tells Wails to quit.
	shutdownFn := func() {
		if a.ctx != nil {
			wailsRuntime.Quit(a.ctx)
		}
	}

	srv := server.New(appState, staticFS, vendorFS, themesFS, iconPNG, version, shutdownFn, a.repackFn)

	fmt.Fprintf(os.Stderr, "Bacalhau: editing %s\n", appState.ChaptersDir())

	// --- Launch Wails window ---

	err := wails.Run(&options.App{
		Title:     "Bacalhau",
		Width:     1280,
		Height:    800,
		MinWidth:  800,
		MinHeight: 600,
		AssetServer: &assetserver.Options{
			Handler: srv.Handler(),
		},
		OnStartup:          a.startup,
		OnShutdown:         a.shutdown,
		Bind:               []interface{}{a},
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}
