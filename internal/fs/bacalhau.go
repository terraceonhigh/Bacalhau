package fs

import (
	"archive/zip"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

// Extract unzips a .bacalhau file into destDir with zip-slip protection.
func Extract(zipPath, destDir string) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return fmt.Errorf("open zip: %w", err)
	}
	defer r.Close()

	cleanDest := filepath.Clean(destDir)

	// Zip-slip protection: verify every member resolves inside destDir.
	for _, f := range r.File {
		target := filepath.Clean(filepath.Join(destDir, f.Name))
		if !strings.HasPrefix(target, cleanDest+string(os.PathSeparator)) && target != cleanDest {
			return fmt.Errorf("unsafe path in archive: %s", f.Name)
		}
	}

	// Extract all members.
	for _, f := range r.File {
		target := filepath.Join(destDir, f.Name)

		if f.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}

		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}

		if err := extractFile(f, target); err != nil {
			return err
		}
	}

	return nil
}

func extractFile(f *zip.File, target string) error {
	rc, err := f.Open()
	if err != nil {
		return err
	}
	defer rc.Close()

	out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, f.Mode())
	if err != nil {
		return err
	}
	defer out.Close()

	_, err = io.Copy(out, rc)
	return err
}

// Repack re-packs the working directory back into the .bacalhau ZIP file.
// It includes chapters/, latex/ (if present), and .git/ (if present) from the
// project root (parent of chaptersDir).
func Repack(chaptersDir, bacalhauFile string) error {
	projectRoot := filepath.Dir(chaptersDir)
	tmpPath := bacalhauFile + ".tmp"

	zf, err := os.Create(tmpPath)
	if err != nil {
		return fmt.Errorf("create temp zip: %w", err)
	}
	zw := zip.NewWriter(zf)

	// Pack chapters/ (skip dot-prefixed dirs and files).
	if err := packDir(zw, chaptersDir, "chapters", true); err != nil {
		zw.Close()
		zf.Close()
		os.Remove(tmpPath)
		return err
	}

	// Pack latex/ if it exists (skip dot-prefixed dirs and files).
	latexDir := filepath.Join(projectRoot, "latex")
	if info, err := os.Stat(latexDir); err == nil && info.IsDir() {
		if err := packDir(zw, latexDir, "latex", true); err != nil {
			zw.Close()
			zf.Close()
			os.Remove(tmpPath)
			return err
		}
	}

	// Pack .git/ if it exists (include everything, no dot-skip).
	gitDir := filepath.Join(projectRoot, ".git")
	if info, err := os.Stat(gitDir); err == nil && info.IsDir() {
		if err := packDir(zw, gitDir, ".git", false); err != nil {
			zw.Close()
			zf.Close()
			os.Remove(tmpPath)
			return err
		}
	}

	if err := zw.Close(); err != nil {
		zf.Close()
		os.Remove(tmpPath)
		return err
	}
	if err := zf.Close(); err != nil {
		os.Remove(tmpPath)
		return err
	}

	return os.Rename(tmpPath, bacalhauFile)
}

// packDir walks srcDir and adds all files into the zip under arcPrefix.
// If skipDot is true, directories and files starting with "." are skipped.
func packDir(zw *zip.Writer, srcDir, arcPrefix string, skipDot bool) error {
	return filepath.Walk(srcDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}

		name := info.Name()
		if skipDot && strings.HasPrefix(name, ".") {
			if info.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}

		if info.IsDir() {
			return nil
		}

		rel, err := filepath.Rel(srcDir, path)
		if err != nil {
			return err
		}
		arcName := filepath.Join(arcPrefix, rel)

		w, err := zw.Create(arcName)
		if err != nil {
			return err
		}

		f, err := os.Open(path)
		if err != nil {
			return err
		}
		defer f.Close()

		_, err = io.Copy(w, f)
		return err
	})
}
