package main

import (
	"embed"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	bfs "github.com/terraceonhigh/Bacalhau/internal/fs"
	"github.com/terraceonhigh/Bacalhau/internal/server"
	"github.com/terraceonhigh/Bacalhau/internal/state"
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

func main() {
	// Parse args: bacalhau [project-path] [--port N]
	args := os.Args[1:]
	port := 3000
	var projectDir string

	for i := 0; i < len(args); i++ {
		if args[i] == "--port" && i+1 < len(args) {
			if p, err := strconv.Atoi(args[i+1]); err == nil {
				port = p
			}
			i++
		} else if !strings.HasPrefix(args[i], "-") {
			projectDir = args[i]
		}
	}

	appState := state.New()

	// Track temp dirs to clean up on exit.
	var tempDirs []string
	defer func() {
		for _, d := range tempDirs {
			os.RemoveAll(d) //nolint:errcheck
		}
	}()

	if projectDir == "" {
		// No project specified -- create empty temp dir for welcome state.
		tmpDir, err := os.MkdirTemp("", "bacalhau-empty-")
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
		tempDirs = append(tempDirs, tmpDir)
		chapDir := filepath.Join(tmpDir, "chapters")
		os.MkdirAll(chapDir, 0o755) //nolint:errcheck
		appState.SetTempDir(tmpDir)
		appState.SetChaptersDir(chapDir)
	} else {
		projectDir, _ = filepath.Abs(projectDir)

		if strings.HasSuffix(projectDir, ".bacalhau") && isFile(projectDir) {
			// Handle .bacalhau file: extract to temp dir.
			appState.SetBacalhauFile(projectDir)

			tmpDir, err := os.MkdirTemp("", "bacalhau-")
			if err != nil {
				fmt.Fprintf(os.Stderr, "Error: %v\n", err)
				os.Exit(1)
			}
			tempDirs = append(tempDirs, tmpDir)

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
			// Handle directory.
			if info, err := os.Stat(projectDir); err != nil || !info.IsDir() {
				fmt.Fprintf(os.Stderr, "Error: not a directory: %s\n", projectDir)
				os.Exit(1)
			}
			appState.SetChaptersDir(projectDir)
		}
	}

	// Repack function: save .bacalhau file if applicable.
	repackFn := func() {
		bf := appState.BacalhauFile()
		cd := appState.ChaptersDir()
		if bf != "" && cd != "" {
			if err := bfs.Repack(cd, bf); err != nil {
				fmt.Fprintf(os.Stderr, "Repack error: %v\n", err)
			}
		}
	}

	// Shutdown signal channel.
	shutdownCh := make(chan struct{}, 1)
	shutdownFn := func() {
		select {
		case shutdownCh <- struct{}{}:
		default:
		}
	}

	srv := server.New(appState, staticFS, vendorFS, themesFS, iconPNG, version, shutdownFn, repackFn)

	// Find an available port.
	var listener net.Listener
	for attempt := port; attempt < port+100; attempt++ {
		l, err := net.Listen("tcp", "127.0.0.1:"+strconv.Itoa(attempt))
		if err == nil {
			listener = l
			port = attempt
			break
		}
	}
	if listener == nil {
		fmt.Fprintln(os.Stderr, "Error: no available port found")
		os.Exit(1)
	}

	url := fmt.Sprintf("http://localhost:%d", port)
	pid := os.Getpid()
	fmt.Fprintf(os.Stderr, "Bacalhau: %s -- editing %s\n", url, appState.ChaptersDir())
	fmt.Fprintf(os.Stderr, "PID: %d -- kill with: kill %d\n", pid, pid)
	fmt.Fprintln(os.Stderr, "Press Ctrl+C to stop.")

	// Start HTTP server in background.
	httpServer := &http.Server{Handler: srv.Handler()}
	go func() {
		if err := httpServer.Serve(listener); err != nil && err != http.ErrServerClosed {
			fmt.Fprintf(os.Stderr, "HTTP error: %v\n", err)
		}
	}()

	// Open browser after a short delay.
	go func() {
		time.Sleep(500 * time.Millisecond)
		openBrowser(url)
	}()

	// Heartbeat watchdog: shut down if browser disappears.
	go func() {
		time.Sleep(30 * time.Second) // Grace period for browser to connect.
		for {
			time.Sleep(15 * time.Second)
			if appState.HeartbeatAge() > 2*time.Minute {
				fmt.Fprintln(os.Stderr, "\nNo heartbeat for 2 minutes -- shutting down.")
				repackFn()
				os.Exit(0)
			}
		}
	}()

	// Signal handling.
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)

	// Block until signal or shutdown request.
	select {
	case sig := <-sigCh:
		fmt.Fprintf(os.Stderr, "\nReceived signal %v, shutting down.\n", sig)
	case <-shutdownCh:
		fmt.Fprintln(os.Stderr, "\nShutdown requested.")
	}

	repackFn()
	httpServer.Close() //nolint:errcheck
}

func openBrowser(url string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", url)
	case "linux":
		cmd = exec.Command("xdg-open", url)
	default:
		return
	}
	cmd.Start() //nolint:errcheck
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}
