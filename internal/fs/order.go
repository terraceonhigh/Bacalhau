// Package fs provides filesystem helpers for reading project trees and
// _order.yaml files.
package fs

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// ReadOrder reads _order.yaml from a directory and returns the ordered list
// of entries. Any on-disk items not listed in the file are appended
// alphabetically.
func ReadOrder(directory string) []string {
	orderFile := filepath.Join(directory, "_order.yaml")

	if _, err := os.Stat(orderFile); err == nil {
		entries := readOrderEntries(orderFile)

		// Collect on-disk items.
		onDisk := make(map[string]bool)
		dirEntries, err := os.ReadDir(directory)
		if err == nil {
			for _, de := range dirEntries {
				name := de.Name()
				if strings.HasPrefix(name, "_") || strings.HasPrefix(name, ".") {
					continue
				}
				if de.IsDir() {
					onDisk[name+"/"] = true
				} else if strings.HasSuffix(name, ".md") {
					onDisk[name] = true
				}
			}
		}

		// Build set of already-listed entries.
		listed := make(map[string]bool, len(entries))
		for _, e := range entries {
			listed[e] = true
		}

		// Append unlisted items alphabetically.
		var extras []string
		for name := range onDisk {
			if !listed[name] {
				extras = append(extras, name)
			}
		}
		sort.Strings(extras)
		entries = append(entries, extras...)
		return entries
	}

	// No _order.yaml — fall back to sorted listing.
	var entries []string
	dirEntries, err := os.ReadDir(directory)
	if err != nil {
		return nil
	}
	for _, de := range dirEntries {
		name := de.Name()
		if strings.HasPrefix(name, "_") || strings.HasPrefix(name, ".") {
			continue
		}
		if de.IsDir() {
			entries = append(entries, name+"/")
		} else if strings.HasSuffix(name, ".md") {
			entries = append(entries, name)
		}
	}
	sort.Strings(entries)
	return entries
}

// ReadOrderRaw reads _order.yaml entries without appending unlisted on-disk
// files.
func ReadOrderRaw(directory string) []string {
	orderFile := filepath.Join(directory, "_order.yaml")
	if _, err := os.Stat(orderFile); err != nil {
		return nil
	}
	return readOrderEntries(orderFile)
}

// WriteOrder writes entries to _order.yaml in the given directory.
func WriteOrder(directory string, entries []string) error {
	path := filepath.Join(directory, "_order.yaml")
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	for _, entry := range entries {
		if _, err := fmt.Fprintf(f, "- %s\n", entry); err != nil {
			return err
		}
	}
	return nil
}

// readOrderEntries parses a _order.yaml file and returns the entries.
func readOrderEntries(path string) []string {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()

	var entries []string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "- ") {
			entry := strings.TrimSpace(line[2:])
			if entry != "" {
				entries = append(entries, entry)
			}
		}
	}
	return entries
}
