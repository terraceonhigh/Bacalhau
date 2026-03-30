package fs

import (
	"bufio"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"unicode"
)

// TreeNode represents a file or directory in the project tree.
type TreeNode struct {
	Type     string     `json:"type"`
	Name     string     `json:"name"`
	Path     string     `json:"path"`
	Heading  string     `json:"heading"`
	Writable bool       `json:"writable,omitempty"`
	Children []TreeNode `json:"children,omitempty"`
}

// BuildTree builds a recursive tree structure for the API, starting from
// chaptersDir. It mirrors the Python build_tree function.
func BuildTree(chaptersDir string) []TreeNode {
	return buildTree(chaptersDir, "")
}

func buildTree(directory, relPrefix string) []TreeNode {
	var nodes []TreeNode

	for _, entry := range ReadOrder(directory) {
		if strings.HasSuffix(entry, "/") {
			dirname := strings.TrimSuffix(entry, "/")
			dirpath := filepath.Join(directory, dirname)
			rel := dirname
			if relPrefix != "" {
				rel = filepath.Join(relPrefix, dirname)
			}

			info, err := os.Stat(dirpath)
			if err != nil || !info.IsDir() {
				continue
			}

			// Get heading from _part.md if it exists.
			heading := titleCase(strings.ReplaceAll(dirname, "-", " "))
			partFile := filepath.Join(dirpath, "_part.md")
			if h := GetHeading(partFile); h != "" {
				heading = h
			}

			children := buildTree(dirpath, rel)
			nodes = append(nodes, TreeNode{
				Type:     "dir",
				Name:     dirname,
				Path:     rel,
				Heading:  heading,
				Children: children,
			})
		} else {
			fpath := filepath.Join(directory, entry)
			rel := entry
			if relPrefix != "" {
				rel = filepath.Join(relPrefix, entry)
			}

			if _, err := os.Stat(fpath); err != nil {
				continue
			}

			heading := GetHeading(fpath)
			if heading == "" {
				heading = entry
			}

			writable := isWritable(fpath)
			nodes = append(nodes, TreeNode{
				Type:     "file",
				Name:     entry,
				Path:     rel,
				Heading:  heading,
				Writable: writable,
			})
		}
	}

	return nodes
}

// GetHeading reads the first markdown heading from a file.
func GetHeading(fpath string) string {
	f, err := os.Open(fpath)
	if err != nil {
		return ""
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line != "" && strings.HasPrefix(line, "#") {
			return strings.TrimSpace(strings.TrimLeft(line, "#"))
		}
	}
	return ""
}

// WalkFiles returns an ordered list of all .md file paths under chaptersDir.
func WalkFiles(chaptersDir string) []string {
	var files []string
	walkFilesRecurse(chaptersDir, &files)
	return files
}

func walkFilesRecurse(directory string, files *[]string) {
	for _, entry := range ReadOrder(directory) {
		if strings.HasSuffix(entry, "/") {
			subdir := filepath.Join(directory, strings.TrimSuffix(entry, "/"))
			if info, err := os.Stat(subdir); err == nil && info.IsDir() {
				walkFilesRecurse(subdir, files)
			}
		} else {
			path := filepath.Join(directory, entry)
			if _, err := os.Stat(path); err == nil {
				*files = append(*files, path)
			}
		}
	}
}

// isWritable checks if a file is writable by the current user.
func isWritable(path string) bool {
	return syscall.Access(path, syscall.O_RDWR) == nil
}

// titleCase capitalises the first letter of each word (replaces deprecated
// strings.Title).
func titleCase(s string) string {
	prev := ' '
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(prev) {
			prev = r
			return unicode.ToTitle(r)
		}
		prev = r
		return r
	}, s)
}
