package server

import (
	"archive/zip"
	"bytes"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	bfs "github.com/terraceonhigh/Bacalhau/internal/fs"
)

var sceneHeadingRe = regexp.MustCompile(`(?m)^(### )(.+)$`)

func (s *Server) exportMarkdown(w http.ResponseWriter, _ *http.Request) {
	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	chapDir := s.state.ChaptersDir()
	var parts []string
	counter := 0
	for _, fpath := range bfs.WalkFiles(chapDir) {
		content, err := os.ReadFile(fpath)
		if err != nil {
			continue
		}
		basename := filepath.Base(fpath)
		text := string(content)
		if basename != "_part.md" && !strings.HasPrefix(basename, "intermezzo-") && basename != "title.md" {
			counter++
			n := counter
			text = sceneHeadingRe.ReplaceAllStringFunc(text, func(m string) string {
				sub := sceneHeadingRe.FindStringSubmatch(m)
				if len(sub) >= 3 {
					return sub[1] + fmt.Sprintf("%d. %s", n, sub[2])
				}
				return m
			})
		}
		parts = append(parts, text)
	}

	assembled := strings.Join(parts, "")
	data := []byte(assembled)

	// Use project name for the filename.
	pname := s.projectName()
	if pname == "" {
		pname = "manuscript"
	}

	w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s.md"`, pname))
	w.Header().Set("Content-Length", fmt.Sprint(len(data)))
	w.Write(data) //nolint:errcheck
}

func (s *Server) exportHTML(w http.ResponseWriter, _ *http.Request) {
	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	chapDir := s.state.ChaptersDir()
	var parts []string
	counter := 0
	for _, fpath := range bfs.WalkFiles(chapDir) {
		content, err := os.ReadFile(fpath)
		if err != nil {
			continue
		}
		basename := filepath.Base(fpath)
		text := string(content)
		if basename != "_part.md" && !strings.HasPrefix(basename, "intermezzo-") && basename != "title.md" {
			counter++
			n := counter
			text = sceneHeadingRe.ReplaceAllStringFunc(text, func(m string) string {
				sub := sceneHeadingRe.FindStringSubmatch(m)
				if len(sub) >= 3 {
					return sub[1] + fmt.Sprintf("%d. %s", n, sub[2])
				}
				return m
			})
		}
		parts = append(parts, text)
	}

	assembled := strings.Join(parts, "")
	pname := s.projectName()
	if pname == "" {
		pname = "Manuscript"
	}

	// Build an HTML page that loads markdown-it from the local vendor endpoint,
	// renders the markdown, and triggers the print dialog.
	html := fmt.Sprintf(`<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>%s</title>
<script src="/vendor/markdown-it.min.js"></script>
<style>
body { font-family: Georgia, serif; max-width: 42em; margin: 2em auto; line-height: 1.6; }
h1,h2,h3 { page-break-after: avoid; }
@media print { body { margin: 0; } }
</style>
</head>
<body>
<div id="content"></div>
<script>
var md = window.markdownit();
var raw = %s;
document.getElementById('content').innerHTML = md.render(raw);
window.print();
</script>
</body>
</html>`, pname, jsonEscapeString(assembled))

	data := []byte(html)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Content-Length", fmt.Sprint(len(data)))
	w.Write(data) //nolint:errcheck
}

func (s *Server) saveZip(w http.ResponseWriter, _ *http.Request) {
	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	chapDir := s.state.ChaptersDir()
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)

	filepath.Walk(chapDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			if strings.HasPrefix(info.Name(), ".") {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasPrefix(info.Name(), ".") {
			return nil
		}
		rel, _ := filepath.Rel(chapDir, path)
		arcName := filepath.Join("chapters", rel)
		fw, err := zw.Create(arcName)
		if err != nil {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		_, err = fw.Write(data)
		return err
	}) //nolint:errcheck
	zw.Close() //nolint:errcheck

	data := buf.Bytes()
	pname := s.projectName()
	if pname == "" {
		pname = "chapters"
	}

	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s.zip"`, pname))
	w.Header().Set("Content-Length", fmt.Sprint(len(data)))
	w.Write(data) //nolint:errcheck
}

func (s *Server) saveBacalhau(w http.ResponseWriter, _ *http.Request) {
	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	chapDir := s.state.ChaptersDir()

	if bf := s.state.BacalhauFile(); bf != "" {
		// In-place save: repack to original file.
		if s.repackFn != nil {
			s.repackFn()
		}
		sendJSON(w, http.StatusOK, map[string]any{
			"message": "Saved",
			"path":    filepath.Base(bf),
		})
		return
	}

	// Download mode: build zip and serve.
	projectRoot := filepath.Dir(chapDir)
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)

	// Pack chapters/.
	packDirToZip(zw, chapDir, "chapters", true) //nolint:errcheck

	// Pack latex/ if it exists.
	latexDir := filepath.Join(projectRoot, "latex")
	if info, err := os.Stat(latexDir); err == nil && info.IsDir() {
		packDirToZip(zw, latexDir, "latex", true) //nolint:errcheck
	}

	// Bundle .git/ so version history travels with the file.
	gitDir := filepath.Join(projectRoot, ".git")
	if info, err := os.Stat(gitDir); err == nil && info.IsDir() {
		packDirToZip(zw, gitDir, ".git", false) //nolint:errcheck
	}

	zw.Close() //nolint:errcheck
	data := buf.Bytes()

	name := s.state.BacalhauName()
	if name == "" {
		name = filepath.Base(projectRoot) + ".bacalhau"
	}
	if !strings.HasSuffix(name, ".bacalhau") {
		name += ".bacalhau"
	}

	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, name))
	w.Header().Set("Content-Length", fmt.Sprint(len(data)))
	w.Write(data) //nolint:errcheck
}

// projectName returns the display name for the current project.
func (s *Server) projectName() string {
	if n := s.state.BacalhauName(); n != "" {
		return strings.TrimSuffix(n, ".bacalhau")
	}
	if f := s.state.BacalhauFile(); f != "" {
		return strings.TrimSuffix(filepath.Base(f), ".bacalhau")
	}
	d := s.state.ChaptersDir()
	if d != "" && filepath.Base(d) == "chapters" {
		d = filepath.Dir(d)
	}
	if d != "" {
		return filepath.Base(d)
	}
	return ""
}

// packDirToZip walks srcDir and adds files to the zip under arcPrefix.
func packDirToZip(zw *zip.Writer, srcDir, arcPrefix string, skipDot bool) error {
	return filepath.Walk(srcDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if skipDot && strings.HasPrefix(info.Name(), ".") {
			if info.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if info.IsDir() {
			return nil
		}
		rel, _ := filepath.Rel(srcDir, path)
		arcName := filepath.Join(arcPrefix, rel)
		fw, werr := zw.Create(arcName)
		if werr != nil {
			return werr
		}
		data, rerr := os.ReadFile(path)
		if rerr != nil {
			return rerr
		}
		_, werr = fw.Write(data)
		return werr
	})
}

// jsonEscapeString returns a JSON-encoded string literal (with quotes).
func jsonEscapeString(s string) string {
	b, _ := jsonMarshal(s)
	return string(b)
}

// jsonMarshal is a small wrapper to avoid importing encoding/json in this file.
func jsonMarshal(v any) ([]byte, error) {
	// We use a simple manual encoding for strings to avoid circular imports.
	// For a string, we produce a JSON string literal.
	str, ok := v.(string)
	if !ok {
		return nil, fmt.Errorf("unsupported type")
	}
	var buf bytes.Buffer
	buf.WriteByte('"')
	for _, r := range str {
		switch r {
		case '"':
			buf.WriteString(`\"`)
		case '\\':
			buf.WriteString(`\\`)
		case '\n':
			buf.WriteString(`\n`)
		case '\r':
			buf.WriteString(`\r`)
		case '\t':
			buf.WriteString(`\t`)
		default:
			if r < 0x20 {
				buf.WriteString(fmt.Sprintf(`\u%04x`, r))
			} else {
				buf.WriteRune(r)
			}
		}
	}
	buf.WriteByte('"')
	return buf.Bytes(), nil
}
