# PDF export revival — archaeology and plan

*Working document on the `feat/pdf-export-revival` branch. Not yet merged. The Architect requested archaeology and an implementation plan; this file holds both. Whoever implements should treat the plan section as load-bearing and the archaeology section as background.*

## What was there

PDF export existed in Bacalhau's Python era. Commit `c9113e0cf3755520b0ac7f77e29df8f9f7a3a177` (March 2026, `refs/tags/penrose-working`) replaced an earlier Pandoc + XeLaTeX pipeline with a pure-Python in-tree implementation. The diff added three things:

```
vendor/md2pdf.py                              347 lines
vendor/mistletoe/                            ~3000 lines  (MIT, markdown parser → AST)
vendor/pdfme/                                ~2000 lines  (MIT, PDF emitter)
```

`md2pdf.py` was the in-house bridge: it walked mistletoe's markdown AST and emitted styled PDF via pdfme, with a side helper that sanitised Unicode characters down to WinAnsiEncoding because pdfme's standard fonts only know `chr(0)–chr(255)`. The commit message captures the intent ("keeping the zero-dep philosophy intact"). The README pivot was characteristic: it had been telling users to `brew install pandoc` and optionally a LaTeX distribution; the new README simply said *PDF export is built in*.

## How it left

Commit `87e5944` — the Wails native-window port — removed it. Not as a deliberate strike against the feature; the entire Python `vendor/` tree went with the rest of Python. The deletion is impersonal: `vendor/md2pdf.py`, `vendor/mistletoe/*`, `vendor/pdfme/*` all gone in a port that mostly cares about replacing the browser with a webview. PDF export was *unported*, not *removed*.

The current `internal/server/api_export.go` carries the half-finished replacement — `exportHTML` builds a self-contained HTML page that auto-triggers `window.print()` on load. The frontend never wired up to it; `static/app.js` fetches `/api/export/pdf`, the backend only serves `/api/export/html`, and the `.pdf` entry in the Save As dropdown 404s in the current build. The README in the `docs/refresh-go-wails-2026-05` branch documents this honestly as currently non-functional.

## Why the print-to-PDF path is right

The Python `pdfme` approach worked because Python was the runtime. In a Go + Wails world, the equivalent move — finding a maintained Go-native PDF emitter that walks markdown and emits styled output with correct unicode and prose-friendly typography — is a much bigger project than it sounds. The candidates are all either unmaintained, half-finished, or thin wrappers around headless Chrome. Three of them shell out to a real browser, which is exactly what we're already running in the Wails webview.

The print-to-PDF path uses the browser we already have. The flow:

1. Assemble the manuscript as HTML — this already works (`exportHTML` in `api_export.go`).
2. Open the HTML in a **secondary Wails window** — Wails v2 supports multiple windows; this becomes the print-target window.
3. Wait for DOM-ready in the secondary window, then call `window.print()` — Wails exposes `runtime.WindowExecJS` for this.
4. The OS print dialog appears with `Save as PDF` as a standard destination. The user picks a location; the OS writes the file.
5. The secondary window closes on print-completion or print-cancel.

This is the helper Fret independently named as the suite-wide answer in `Pipa/Correspondance/03-the-bundle-is-bigger-than-promised/letter.md`. The suite-wide framing is exactly right: Bacalhau wants it for manuscripts, Pipa needs it for Marp slide decks, Lapis could use it for an active-sheet print. Implementing it three times would be a small but real category mistake.

## Plan

### Phase 1 — Bacalhau, in-tree

Implement the helper as a Bacalhau-internal package `internal/pdfprint/` with the *intent* that it becomes a shared module later. That means: keep the package surface small and free of Bacalhau-specific assumptions; take HTML, suggested filename, and Wails app context as inputs; expose one function.

```go
// Package pdfprint provides a Wails-secondary-window print-to-PDF helper.
// Designed to be extracted into github.com/terraceonhigh/suite-foundation/pdfprint
// once the foundation module is real. No project-specific imports.
package pdfprint

import "context"

// Print opens html in a secondary Wails window, waits for DOM-ready,
// triggers window.print(), and closes the window on print-completion or
// cancel. suggestedName seeds the OS save-as dialog. Returns nil on
// successful print or user-cancel; non-nil on Wails-side errors.
func Print(ctx context.Context, html []byte, suggestedName string) error {
    // 1. WindowCreate with the html as initial content (or serve via a
    //    one-shot in-memory route).
    // 2. WindowExecJS to ensure print() runs after DOMContentLoaded.
    // 3. Hook beforeunload or runtime event for window close.
    // ...
}
```

The Bacalhau wiring is then:

- `static/app.js` `exportPDF()` changes its fetch from `/api/export/pdf` to `/api/export/html`. The response body is HTML.
- A new Wails-bound method `app.PrintHTML(html, suggestedName string) error` calls `pdfprint.Print` with the app context.
- The frontend posts the HTML to `PrintHTML` rather than trying to download-and-rename.
- The dropdown's `.pdf` entry routes through this; the broken `/api/export/pdf` endpoint can either become an alias or be removed.

The Wails secondary-window API needs the binary in hand to verify behaviour — that's morning work, not 02:00 work, which is why this plan sits on a branch rather than landing as a working commit tonight. The contract above is what the implementer should target.

### Phase 2 — extract to foundation

Once the helper is exercised in Bacalhau and the surface has stabilised, extract it as the first inhabitant of `github.com/terraceonhigh/suite-foundation/pdfprint`. See `Correspondance/02-on-keeping-the-three-in-sync/letter.md` for the foundation-module rationale.

### Phase 3 — Pipa and Lapis

Pipa's `api_export.go` already serves Marp's HTML output via `/api/export/html`. The wiring is identical: bind `PrintHTML` on the Pipa side, route the dropdown's eventual `.pdf` option through it.

Lapis hasn't asked for PDF in v0.1. When it does, the same shape applies — assemble the active sheet as HTML (or all sheets, depending on UX decision), call `PrintHTML`.

### Phase 4 — gh-pages-app branch (browser-only variant)

The `gh-pages-app` branch is the web deployment — Bacalhau running in a regular browser, no Wails webview, no native windows. PDF export there does *not* need the secondary-window helper because the host *is* a browser. The implementation collapses to:

1. The existing `exportHTML` endpoint already serves HTML with inline `window.print()` script — works as-is.
2. The frontend opens the response in a new tab (`window.open()` with the HTML as a blob URL, or by fetching and writing to `document.write` of a popup). The new tab loads, the inline script fires `window.print()`, the user gets the OS print dialog with Save-as-PDF available.
3. Total work: roughly fifty lines of frontend JS, no Go changes.

This variant should land on `gh-pages-app` separately from the desktop work. Whoever implements the desktop version on `main` is responsible for porting the browser variant to `gh-pages-app` as a small follow-up commit. **Do not let the gh-pages-app branch ship a v3.x release without this** — its README will be wrong otherwise, and a browser-only Bacalhau that can't print is a missing feature, not a fancy one.

## Testing plan

For the desktop variant:

1. Build with `./build-app.sh dev`, open against `demo/chapters/`.
2. Save As → `.pdf`. Verify the OS print dialog appears.
3. Print to `Save as PDF`. Verify the resulting PDF opens, contains the manuscript with auto-numbered scene headings, and has reasonable typography (Charter or Georgia, 38em column, proper italics for scene headings).
4. Cancel the dialog. Verify the secondary window closes and Bacalhau is responsive.
5. Test with a `.bacalhau` file open (temp-dir mode) as well as a folder.

For the browser variant (gh-pages-app):

1. `go run` Bacalhau in browser mode against `demo/chapters/`.
2. Save As → `.pdf` in browser. Verify a new tab opens with the rendered manuscript and the OS print dialog appears in that tab.

## Why this isn't merged tonight

The Wails secondary-window API has subtleties (DOM-ready signalling, lifecycle hooks, focus stealing, multi-monitor behaviour) that want a binary in hand to verify rather than a guess-and-commit at 02:00. The contract above is firm; the implementation wants morning attention. Reviewers should treat this branch as a *design document with intent to implement*, not as merge-ready code.

The follow-up branch will replace this doc with the actual `internal/pdfprint/` package, the `app.PrintHTML` binding in `main.go`, the JS routing change in `static/app.js`, and the obsolete-endpoint cleanup in `internal/server/api_export.go`.

---

*Branch: `feat/pdf-export-revival`. Parent: `main`. Sibling: `correspondance/inaugurate` (which carries the letter that gestures at this work).*
