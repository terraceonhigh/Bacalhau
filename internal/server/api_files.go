package server

import (
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	bfs "github.com/terraceonhigh/Bacalhau/internal/fs"
)

var slugRe = regexp.MustCompile(`[^a-z0-9-]`)
var nameRe = regexp.MustCompile(`[^a-zA-Z0-9_. -]`)
var headingRe = regexp.MustCompile(`(?m)^(### ).+$`)

func (s *Server) newChapter(w http.ResponseWriter, r *http.Request) {
	body, err := readBody(r)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid request body"})
		return
	}

	rawSlug, _ := body["slug"].(string)
	slug := strings.Trim(slugRe.ReplaceAllString(strings.ToLower(rawSlug), "-"), "-")
	parentDir, _ := body["dir"].(string)
	auto, _ := body["autoIncrement"].(bool)

	if slug == "" {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid slug"})
		return
	}

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	var dirpath string
	if parentDir != "" {
		dirpath, err = s.resolvePath(parentDir)
		if err != nil {
			sendJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
	} else {
		dirpath = s.state.ChaptersDir()
	}

	fname := slug + ".md"
	fpath := filepath.Join(dirpath, fname)
	if fileExists(fpath) {
		if auto {
			n := 2
			for fileExists(filepath.Join(dirpath, slug+"-"+itoa(n)+".md")) {
				n++
			}
			slug = slug + "-" + itoa(n)
			fname = slug + ".md"
			fpath = filepath.Join(dirpath, fname)
		} else {
			sendJSON(w, http.StatusConflict, map[string]any{"error": "Already exists: " + fname})
			return
		}
	}

	title := strings.Title(strings.ReplaceAll(slug, "-", " ")) //nolint:staticcheck

	// Update _order.yaml BEFORE creating the file.
	position := intFromBody(body, "position", -1)
	order := bfs.ReadOrderRaw(dirpath)
	if order == nil {
		order = []string{}
	}
	if position >= 0 && position <= len(order) {
		order = insertAt(order, position, fname)
	} else {
		order = append(order, fname)
	}
	bfs.WriteOrder(dirpath, order) //nolint:errcheck

	os.WriteFile(fpath, []byte("### "+title+"\n\n"), 0o644) //nolint:errcheck

	chapDir := s.state.ChaptersDir()
	relpath, _ := filepath.Rel(chapDir, fpath)
	sendJSON(w, http.StatusOK, map[string]any{
		"path":    relpath,
		"fname":   fname,
		"message": "Created " + relpath,
	})
}

func (s *Server) newDir(w http.ResponseWriter, r *http.Request) {
	body, err := readBody(r)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid request body"})
		return
	}

	rawName, _ := body["name"].(string)
	name := strings.Trim(slugRe.ReplaceAllString(strings.ToLower(rawName), "-"), "-")
	parentDir, _ := body["dir"].(string)
	auto, _ := body["autoIncrement"].(bool)

	if name == "" {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid name"})
		return
	}

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	var dirpath string
	if parentDir != "" {
		dirpath, err = s.resolvePath(parentDir)
		if err != nil {
			sendJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
	} else {
		dirpath = s.state.ChaptersDir()
	}

	newDirpath := filepath.Join(dirpath, name)
	if fileExists(newDirpath) {
		if auto {
			n := 2
			for fileExists(filepath.Join(dirpath, name+"-"+itoa(n))) {
				n++
			}
			name = name + "-" + itoa(n)
			newDirpath = filepath.Join(dirpath, name)
		} else {
			sendJSON(w, http.StatusConflict, map[string]any{"error": "Already exists: " + name})
			return
		}
	}

	// Update parent _order.yaml BEFORE creating the dir.
	position := intFromBody(body, "position", -1)
	order := bfs.ReadOrderRaw(dirpath)
	if order == nil {
		order = []string{}
	}
	entry := name + "/"
	if position >= 0 && position <= len(order) {
		order = insertAt(order, position, entry)
	} else {
		order = append(order, entry)
	}
	bfs.WriteOrder(dirpath, order) //nolint:errcheck

	os.MkdirAll(newDirpath, 0o755) //nolint:errcheck
	bfs.WriteOrder(newDirpath, []string{}) //nolint:errcheck

	sendJSON(w, http.StatusOK, map[string]any{
		"name":    name,
		"message": "Created " + name + "/",
	})
}

func (s *Server) renameItem(w http.ResponseWriter, r *http.Request) {
	body, err := readBody(r)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid request body"})
		return
	}

	oldPath, _ := body["path"].(string)
	newName, _ := body["newName"].(string)
	itemType, _ := body["type"].(string)
	if itemType == "" {
		itemType = "file"
	}

	newName = strings.TrimSpace(newName)
	if newName == "" {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Name is required"})
		return
	}

	// Sanitise.
	newName = strings.Trim(nameRe.ReplaceAllString(newName, "-"), "-")
	if newName == "" {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid name"})
		return
	}

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	oldAbs, err := s.resolvePath(oldPath)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}

	parentDir := filepath.Dir(oldAbs)
	oldName := filepath.Base(oldAbs)

	if itemType == "file" && !strings.HasSuffix(newName, ".md") {
		newName += ".md"
	}
	newAbs := filepath.Join(parentDir, newName)

	if fileExists(newAbs) {
		sendJSON(w, http.StatusConflict, map[string]any{"error": "Already exists: " + newName})
		return
	}

	// Update _order.yaml BEFORE renaming on disk.
	oldEntry := oldName
	newEntry := newName
	if itemType == "dir" {
		oldEntry += "/"
		newEntry += "/"
	}

	orderFile := filepath.Join(parentDir, "_order.yaml")
	if fileExists(orderFile) {
		raw := bfs.ReadOrderRaw(parentDir)
		for i, e := range raw {
			if e == oldEntry {
				raw[i] = newEntry
				break
			}
		}
		bfs.WriteOrder(parentDir, raw) //nolint:errcheck
	}

	os.Rename(oldAbs, newAbs) //nolint:errcheck

	// Update heading inside the file to match the new name.
	if itemType == "file" && fileExists(newAbs) {
		title := strings.Title(strings.ReplaceAll(strings.TrimSuffix(newName, ".md"), "-", " ")) //nolint:staticcheck
		content, err := os.ReadFile(newAbs)
		if err == nil {
			updated := headingRe.ReplaceAll(content, []byte("${1}"+title))
			os.WriteFile(newAbs, updated, 0o644) //nolint:errcheck
		}
	}

	chapDir := s.state.ChaptersDir()
	newRelpath, _ := filepath.Rel(chapDir, newAbs)
	sendJSON(w, http.StatusOK, map[string]any{
		"message": "Renamed \u2192 " + newName,
		"newPath": newRelpath,
	})
}

func (s *Server) moveItem(w http.ResponseWriter, r *http.Request) {
	body, err := readBody(r)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Invalid request body"})
		return
	}

	src, _ := body["src"].(string)
	srcType, _ := body["src_type"].(string)
	if srcType == "" {
		srcType = "file"
	}
	destDir, _ := body["dest_dir"].(string)
	position := intFromBody(body, "position", -1)

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	srcAbs, err := s.resolvePath(src)
	if err != nil {
		sendJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	srcName := filepath.Base(srcAbs)
	srcParent := filepath.Dir(srcAbs)

	var destAbs string
	if destDir != "" {
		destAbs, err = s.resolvePath(destDir)
		if err != nil {
			sendJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
	} else {
		destAbs = s.state.ChaptersDir()
	}

	if !fileExists(srcAbs) {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found: " + src})
		return
	}

	entryName := srcName
	if srcType == "dir" {
		entryName += "/"
	}

	sameDir := filepath.Clean(srcParent) == filepath.Clean(destAbs)

	if sameDir {
		// Reorder within the same directory.
		order := bfs.ReadOrder(srcParent)
		oldIdx := -1
		for i, e := range order {
			if e == entryName {
				oldIdx = i
				break
			}
		}
		if oldIdx < 0 {
			sendJSON(w, http.StatusBadRequest, map[string]any{"error": "Not in order: " + entryName})
			return
		}
		order = append(order[:oldIdx], order[oldIdx+1:]...)

		insertPos := position
		if position < 0 || position >= len(order) {
			insertPos = len(order)
		} else if oldIdx < position {
			insertPos = position - 1
		}
		order = insertAt(order, insertPos, entryName)
		bfs.WriteOrder(srcParent, order) //nolint:errcheck
	} else {
		// Move between directories.
		srcOrder := bfs.ReadOrder(srcParent)
		filtered := make([]string, 0, len(srcOrder))
		for _, e := range srcOrder {
			if e != entryName {
				filtered = append(filtered, e)
			}
		}
		bfs.WriteOrder(srcParent, filtered) //nolint:errcheck

		newPath := filepath.Join(destAbs, srcName)
		os.Rename(srcAbs, newPath) //nolint:errcheck

		destOrder := bfs.ReadOrder(destAbs)
		// Remove if already present.
		cleanDest := make([]string, 0, len(destOrder))
		for _, e := range destOrder {
			if e != entryName {
				cleanDest = append(cleanDest, e)
			}
		}
		if position < 0 || position >= len(cleanDest) {
			cleanDest = append(cleanDest, entryName)
		} else {
			cleanDest = insertAt(cleanDest, position, entryName)
		}
		bfs.WriteOrder(destAbs, cleanDest) //nolint:errcheck
	}

	sendJSON(w, http.StatusOK, map[string]any{"message": "Moved " + srcName})
}

func (s *Server) copyDir(w http.ResponseWriter, r *http.Request) {
	p := r.PathValue("path")
	relpath := strings.TrimSuffix(p, "/copy")

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	abspath, err := s.resolvePath(relpath)
	if err != nil {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
		return
	}
	info, err := os.Stat(abspath)
	if err != nil || !info.IsDir() {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
		return
	}

	parent := filepath.Dir(abspath)
	basename := filepath.Base(abspath)

	n := 1
	var newName string
	for {
		suffix := "-copy"
		if n > 1 {
			suffix = "-copy-" + itoa(n)
		}
		newName = basename + suffix
		if !fileExists(filepath.Join(parent, newName)) {
			break
		}
		n++
	}

	// Update _order.yaml BEFORE copying.
	order := bfs.ReadOrderRaw(parent)
	entry := basename + "/"
	newEntry := newName + "/"
	idx := len(order)
	for i, e := range order {
		if e == entry {
			idx = i + 1
			break
		}
	}
	order = insertAt(order, idx, newEntry)
	bfs.WriteOrder(parent, order) //nolint:errcheck

	copyTree(abspath, filepath.Join(parent, newName)) //nolint:errcheck

	sendJSON(w, http.StatusOK, map[string]any{"message": "Copied to " + newName + "/"})
}

func (s *Server) deleteDir(w http.ResponseWriter, r *http.Request) {
	relpath := r.PathValue("path")

	s.fsMu.Lock()
	defer s.fsMu.Unlock()

	abspath, err := s.resolvePath(relpath)
	if err != nil {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": err.Error()})
		return
	}
	info, err := os.Stat(abspath)
	if err != nil || !info.IsDir() {
		sendJSON(w, http.StatusNotFound, map[string]any{"error": "Not found"})
		return
	}

	parent := filepath.Dir(abspath)
	dirname := filepath.Base(abspath)
	os.RemoveAll(abspath) //nolint:errcheck

	order := bfs.ReadOrder(parent)
	entry := dirname + "/"
	filtered := make([]string, 0, len(order))
	for _, e := range order {
		if e != entry {
			filtered = append(filtered, e)
		}
	}
	bfs.WriteOrder(parent, filtered) //nolint:errcheck

	sendJSON(w, http.StatusOK, map[string]any{"message": "Deleted " + relpath + "/"})
}

// intFromBody extracts an int from a body map, returning def if not present.
func intFromBody(body map[string]any, key string, def int) int {
	v, ok := body[key]
	if !ok || v == nil {
		return def
	}
	switch n := v.(type) {
	case float64:
		return int(n)
	case int:
		return n
	default:
		return def
	}
}

// copyTree recursively copies a directory.
func copyTree(src, dst string) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, path)
		target := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(target, info.Mode())
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, info.Mode())
	})
}
