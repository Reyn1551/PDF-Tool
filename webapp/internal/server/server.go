// Package server is the HTTP layer: JSON APIs for every operation plus static
// SPA serving. It keeps one open PDFium document per session and a byte-budgeted
// thumbnail cache, and re-opens the document after each edit so the handle
// always matches the current bytes.
package server

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"io"
	"io/fs"
	"log"
	"net"
	"net/http"
	"path"
	"strconv"
	"strings"
	"sync"
	"time"

	"pdftoolweb/internal/ops"
	"pdftoolweb/internal/pdfiumx"
	"pdftoolweb/internal/store"
)

// Server holds shared state.
type Server struct {
	// apiMu serializes every /api/ request. PDFium handles are closed and
	// replaced per edit; without this lock two tabs editing the same session
	// could race a use-after-free on the native handle. The engine is
	// single-threaded anyway, so this costs nothing.
	apiMu  sync.Mutex
	Pool   *pdfiumx.Pool
	Store  *store.Store
	Thumbs *store.ThumbCache
	// MaxUpload is the largest accepted PDF in bytes.
	MaxUpload int64
	// UploadsPerHour caps uploads per client IP (0 disables the limit).
	UploadsPerHour int
	limiter        *rateLimiter
	// Static is the embedded web assets (index.html, app.js, style.css).
	Static fs.FS
}

// Resource bounds, chosen for a 1 GB box.
const (
	maxSessions      = 64 // open documents before upload returns 503
	sessionTTL       = 30 * time.Minute
	uploadMemBytes   = 8 << 20  // multipart parts above this spill to temp files
	imageUploadMax   = 10 << 20 // watermark image cap
	watermarkPxMax   = 4096     // max width/height of a watermark image
	thumbWidthMinmax = 2048     // clamp for thumbnail render width
	fullWidthMax     = 4096     // clamp for full-page / export render width
)

// New builds a Server.
func New(pool *pdfiumx.Pool, static fs.FS) *Server {
	s := &Server{
		Pool:           pool,
		Store:          store.NewStore(),
		Thumbs:         store.NewThumbCache(64 << 20), // 64 MB thumbnail budget
		MaxUpload:      150 << 20,                     // 150 MB
		UploadsPerHour: 30,
		limiter:        newRateLimiter(),
		Static:         static,
	}
	go s.janitor()
	return s
}

// janitor reclaims idle sessions and expires rate-limit windows.
func (s *Server) janitor() {
	t := time.NewTicker(time.Minute)
	for range t.C {
		s.apiMu.Lock()
		for _, ses := range s.Store.Sweep(sessionTTL) {
			s.Pool.CloseDocument(ses.Doc)
			s.Thumbs.InvalidateVersion(ses.Version)
		}
		s.apiMu.Unlock()
		s.limiter.cleanup()
	}
}

// Handler returns the root http.Handler.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()

	// system
	mux.HandleFunc("GET /api/health", s.health)

	// session lifecycle
	mux.HandleFunc("POST /api/upload", s.upload)
	mux.HandleFunc("GET /api/session/{id}", s.sessionInfo)
	mux.HandleFunc("DELETE /api/session/{id}", s.deleteSession)

	// page data
	mux.HandleFunc("GET /api/session/{id}/page/{n}/thumb", s.thumb)
	mux.HandleFunc("GET /api/session/{id}/page/{n}/full", s.fullPage)
	mux.HandleFunc("GET /api/session/{id}/page/{n}/text", s.pageText)
	mux.HandleFunc("GET /api/session/{id}/metadata", s.metadata)

	// organize
	mux.HandleFunc("POST /api/session/{id}/delete", s.deletePages)
	mux.HandleFunc("POST /api/session/{id}/rotate", s.rotate)
	mux.HandleFunc("POST /api/session/{id}/reorder", s.reorder)
	mux.HandleFunc("POST /api/session/{id}/reverse", s.reverse)
	mux.HandleFunc("POST /api/session/{id}/split", s.split)
	mux.HandleFunc("POST /api/session/{id}/merge", s.merge)

	// security / decoration
	mux.HandleFunc("POST /api/session/{id}/watermark", s.watermark)
	mux.HandleFunc("POST /api/session/{id}/redact", s.redact)
	mux.HandleFunc("POST /api/session/{id}/protect", s.protect)
	mux.HandleFunc("POST /api/session/{id}/unlock", s.unlock)
	mux.HandleFunc("POST /api/session/{id}/compress", s.compress)

	// export
	mux.HandleFunc("GET /api/session/{id}/download", s.download)
	mux.HandleFunc("GET /api/session/{id}/export/text", s.exportText)
	mux.HandleFunc("GET /api/session/{id}/export/images", s.exportImages)

	// static SPA (everything else)
	mux.Handle("/", spaHandler(s.Static))

	return withSecurityHeaders(withRecover(s.withAPILock(mux)))
}

// ---- middleware ---------------------------------------------------------

// withAPILock serializes all API requests (see Server.apiMu). Static assets
// stay lock-free.
func (s *Server) withAPILock(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/api/") {
			s.apiMu.Lock()
			defer s.apiMu.Unlock()
		}
		next.ServeHTTP(w, r)
	})
}

// withSecurityHeaders locks the app down to same-origin, self-hosted assets
// only. There are no third-party scripts, fonts, or endpoints; the CSP makes
// that a guarantee rather than a promise.
func withSecurityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
		h.Set("Content-Security-Policy",
			"default-src 'self'; script-src 'self'; style-src 'self'; "+
				"img-src 'self' data:; connect-src 'self'; font-src 'self'; "+
				"object-src 'none'; base-uri 'none'; form-action 'self'; "+
				"frame-ancestors 'none'")
		next.ServeHTTP(w, r)
	})
}

func withRecover(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if e := recover(); e != nil {
				log.Printf("panic: %v", e)
				writeErr(w, http.StatusInternalServerError, "internal error")
			}
		}()
		next.ServeHTTP(w, r)
	})
}

// ---- rate limiting ------------------------------------------------------

// rateLimiter is a fixed-window per-key counter. Keys are client IPs; the
// window is one hour. Deliberately simple: exactness matters less than
// bounding upload abuse on a public instance.
type rateLimiter struct {
	mu   sync.Mutex
	hits map[string]*rlWindow
}

type rlWindow struct {
	n     int
	reset time.Time
}

func newRateLimiter() *rateLimiter { return &rateLimiter{hits: map[string]*rlWindow{}} }

// allow reports whether the key is under the per-window limit and counts it.
func (l *rateLimiter) allow(key string, limit int) bool {
	if limit <= 0 {
		return true
	}
	now := time.Now()
	l.mu.Lock()
	defer l.mu.Unlock()
	w := l.hits[key]
	if w == nil || now.After(w.reset) {
		l.hits[key] = &rlWindow{n: 1, reset: now.Add(time.Hour)}
		return true
	}
	if w.n >= limit {
		return false
	}
	w.n++
	return true
}

func (l *rateLimiter) cleanup() {
	now := time.Now()
	l.mu.Lock()
	defer l.mu.Unlock()
	for k, w := range l.hits {
		if now.After(w.reset) {
			delete(l.hits, k)
		}
	}
}

// clientIP identifies the caller. Behind the documented nginx setup
// (loopback proxy with X-Real-IP) the header is trusted; otherwise the socket
// address is used, so the header cannot be spoofed to dodge the limit.
func clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	if xri := r.Header.Get("X-Real-IP"); xri != "" {
		if ip := net.ParseIP(strings.TrimSpace(xri)); ip != nil && isLoopback(host) {
			return ip.String()
		}
	}
	return host
}

func isLoopback(host string) bool {
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// ---- helpers ------------------------------------------------------------

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

func decodeBody(w http.ResponseWriter, r *http.Request, v any) error {
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	dec := json.NewDecoder(r.Body)
	return dec.Decode(v)
}

// sanitizeName reduces an untrusted filename to a safe basename: no path
// separators, no control characters, no quotes (which would break or inject
// into Content-Disposition).
func sanitizeName(name string) string {
	name = path.Base(strings.ReplaceAll(name, "\\", "/"))
	var b strings.Builder
	for _, r := range name {
		if r < 32 || r == 127 || r == '"' || r == '\\' {
			continue
		}
		b.WriteRune(r)
		if b.Len() >= 200 {
			break
		}
	}
	out := strings.TrimSpace(b.String())
	if out == "" || out == "." || out == "/" {
		return "document.pdf"
	}
	return out
}

// disposition builds an attachment header with a sanitized filename.
func disposition(name, ext string) string {
	base := strings.TrimSuffix(sanitizeName(name), ".pdf")
	return `attachment; filename="` + base + ext + `"`
}

// clampInt bounds a query parameter.
func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// getSession loads a session or writes a 404.
func (s *Server) getSession(w http.ResponseWriter, id string) (*store.Session, bool) {
	ses := s.Store.Get(id)
	if ses == nil {
		writeErr(w, http.StatusNotFound, "session not found")
		return nil, false
	}
	return ses, true
}

// apply re-opens the result bytes as a fresh document and bumps the session.
// password is used when the result is encrypted.
func (s *Server) apply(ses *store.Session, data []byte, pages int, password string) error {
	s.Pool.CloseDocument(ses.Doc)
	doc, err := s.Pool.Open(data, password)
	if err != nil {
		return err
	}
	ses.Doc = doc
	ses.Password = password
	s.Store.Bump(ses, pages, int64(len(data)))
	s.Thumbs.InvalidateVersion(ses.Version - 1)
	return nil
}

// ---- system -------------------------------------------------------------

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	entries, used := s.Thumbs.Stats()
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":         true,
		"sessions":   len(s.Store.All()),
		"thumbCache": map[string]any{"entries": entries, "bytes": used},
	})
}

// ---- session lifecycle --------------------------------------------------

func (s *Server) upload(w http.ResponseWriter, r *http.Request) {
	if !s.limiter.allow(clientIP(r), s.UploadsPerHour) {
		writeErr(w, http.StatusTooManyRequests, "too many uploads, try again later")
		return
	}
	if s.Store.Count() >= maxSessions {
		writeErr(w, http.StatusServiceUnavailable, "server is busy, close some tabs or try again later")
		return
	}
	// Hard body cap first, then keep only 8 MB in RAM; larger parts spill to
	// temp files that are removed as soon as the request finishes.
	r.Body = http.MaxBytesReader(w, r.Body, s.MaxUpload+(1<<20))
	if err := r.ParseMultipartForm(uploadMemBytes); err != nil {
		writeErr(w, http.StatusBadRequest, "upload too large or malformed: "+err.Error())
		return
	}
	defer func() {
		if r.MultipartForm != nil {
			_ = r.MultipartForm.RemoveAll()
		}
	}()
	file, hdr, err := r.FormFile("file")
	if err != nil {
		writeErr(w, http.StatusBadRequest, "missing file field: "+err.Error())
		return
	}
	defer file.Close()
	if hdr.Size > s.MaxUpload {
		writeErr(w, http.StatusRequestEntityTooLarge, "file exceeds limit")
		return
	}
	data, err := io.ReadAll(io.LimitReader(file, s.MaxUpload+1))
	if err != nil {
		writeErr(w, http.StatusBadRequest, "read error: "+err.Error())
		return
	}
	if int64(len(data)) > s.MaxUpload {
		writeErr(w, http.StatusRequestEntityTooLarge, "file exceeds limit")
		return
	}
	name := sanitizeName(hdr.Filename)
	doc, pages, err := ops.OpenBytes(s.Pool, data, "")
	if err != nil {
		writeErr(w, http.StatusBadRequest, "cannot open PDF: "+err.Error())
		return
	}
	ses := s.Store.Add(name, doc, pages, int64(len(data)))
	writeJSON(w, http.StatusOK, map[string]any{
		"id":      ses.ID,
		"name":    ses.Name,
		"pages":   ses.Pages,
		"size":    ses.Size,
		"version": ses.Version,
	})
}

func (s *Server) sessionInfo(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	meta, _ := s.Pool.Metadata(ses.Doc)
	writeJSON(w, http.StatusOK, map[string]any{
		"id":       ses.ID,
		"name":     ses.Name,
		"pages":    ses.Pages,
		"size":     ses.Size,
		"version":  ses.Version,
		"metadata": meta,
	})
}

func (s *Server) deleteSession(w http.ResponseWriter, r *http.Request) {
	ses := s.Store.Delete(r.PathValue("id"))
	if ses == nil {
		writeErr(w, http.StatusNotFound, "session not found")
		return
	}
	s.Pool.CloseDocument(ses.Doc)
	s.Thumbs.InvalidateVersion(ses.Version)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

// ---- page data ----------------------------------------------------------

func (s *Server) thumb(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	n, _ := strconv.Atoi(r.PathValue("n"))
	width := clampInt(queryInt(r, "w", 140), 32, thumbWidthMinmax)
	if n < 0 || n >= ses.Pages {
		writeErr(w, http.StatusNotFound, "page out of range")
		return
	}
	if data, hit := s.Thumbs.Get(ses.Version, n, width); hit {
		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Cache-Control", "private, max-age=300")
		_, _ = w.Write(data)
		return
	}
	data, rw, rh, err := s.Pool.RenderPNG(ses.Doc, n, width, 0)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "render: "+err.Error())
		return
	}
	s.Thumbs.Put(ses.Version, n, width, data)
	w.Header().Set("Content-Type", "image/png")
	w.Header().Set("Cache-Control", "private, max-age=300")
	w.Header().Set("X-Render-Width", strconv.Itoa(rw))
	w.Header().Set("X-Render-Height", strconv.Itoa(rh))
	_, _ = w.Write(data)
}

func (s *Server) fullPage(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	n, _ := strconv.Atoi(r.PathValue("n"))
	width := clampInt(queryInt(r, "w", 1100), 32, fullWidthMax)
	if n < 0 || n >= ses.Pages {
		writeErr(w, http.StatusNotFound, "page out of range")
		return
	}
	data, _, _, err := s.Pool.RenderPNG(ses.Doc, n, width, 0)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "render: "+err.Error())
		return
	}
	w.Header().Set("Content-Type", "image/png")
	_, _ = w.Write(data)
}

func (s *Server) pageText(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	n, _ := strconv.Atoi(r.PathValue("n"))
	if n < 0 || n >= ses.Pages {
		writeErr(w, http.StatusNotFound, "page out of range")
		return
	}
	text, err := s.Pool.PageText(ses.Doc, n)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"page": n, "text": text})
}

func (s *Server) metadata(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	meta, _ := s.Pool.Metadata(ses.Doc)
	writeJSON(w, http.StatusOK, meta)
}

// ---- organize -----------------------------------------------------------

func (s *Server) deletePages(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	var body struct {
		Pages []int `json:"pages"`
	}
	if err := decodeBody(w, r, &body); err != nil || len(body.Pages) == 0 {
		writeErr(w, http.StatusBadRequest, "pages required")
		return
	}
	data, pages, err := ops.DeletePages(s.Pool, ses.Doc, body.Pages)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := s.apply(ses, data, pages, ses.Password); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	s.respondEdit(w, ses)
}

func (s *Server) rotate(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	var body struct {
		Pages []int `json:"pages"`
		Deg   int   `json:"deg"`
	}
	if err := decodeBody(w, r, &body); err != nil || len(body.Pages) == 0 {
		writeErr(w, http.StatusBadRequest, "pages required")
		return
	}
	data, pages, err := ops.Rotate(s.Pool, ses.Doc, body.Pages, body.Deg)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := s.apply(ses, data, pages, ses.Password); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	s.respondEdit(w, ses)
}

func (s *Server) reorder(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	var body struct {
		Order []int `json:"order"`
	}
	if err := decodeBody(w, r, &body); err != nil || len(body.Order) == 0 {
		writeErr(w, http.StatusBadRequest, "order required")
		return
	}
	data, pages, err := ops.Reorder(s.Pool, ses.Doc, body.Order)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := s.apply(ses, data, pages, ses.Password); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	s.respondEdit(w, ses)
}

func (s *Server) reverse(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	data, pages, err := ops.Reverse(s.Pool, ses.Doc)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := s.apply(ses, data, pages, ses.Password); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	s.respondEdit(w, ses)
}

func (s *Server) split(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	var body struct {
		Start int `json:"start"`
		End   int `json:"end"`
	}
	if err := decodeBody(w, r, &body); err != nil || body.Start < 1 {
		writeErr(w, http.StatusBadRequest, "start/end required (1-based)")
		return
	}
	if body.End < body.Start {
		body.End = body.Start
	}
	data, pages, err := ops.SplitRange(s.Pool, ses.Doc, body.Start, body.End)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	// Split produces a NEW session (the extracted subset), not a mutation.
	doc, _, err := ops.OpenBytes(s.Pool, data, "")
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	name := fmt.Sprintf("%s (pages %d-%d)", ses.Name, body.Start, body.End)
	ns := s.Store.Add(name, doc, pages, int64(len(data)))
	writeJSON(w, http.StatusOK, map[string]any{"id": ns.ID, "name": ns.Name, "pages": ns.Pages, "version": ns.Version})
}

func (s *Server) merge(w http.ResponseWriter, r *http.Request) {
	var body struct {
		IDs []string `json:"ids"`
	}
	if err := decodeBody(w, r, &body); err != nil || len(body.IDs) < 2 {
		writeErr(w, http.StatusBadRequest, "at least two session ids required")
		return
	}
	// Dedupe: a repeated id would close the same document twice.
	seen := map[string]bool{}
	ids := make([]string, 0, len(body.IDs))
	for _, id := range body.IDs {
		if !seen[id] {
			seen[id] = true
			ids = append(ids, id)
		}
	}
	if len(ids) < 2 {
		writeErr(w, http.StatusBadRequest, "at least two distinct session ids required")
		return
	}
	docs := make([]ops.Doc, 0, len(ids))
	for _, id := range ids {
		ses := s.Store.Get(id)
		if ses == nil {
			writeErr(w, http.StatusNotFound, "session "+id+" not found")
			return
		}
		docs = append(docs, ses.Doc)
	}
	data, pages, err := ops.Merge(s.Pool, docs)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	doc, _, err := ops.OpenBytes(s.Pool, data, "")
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	// Replace the first session with the merged result; drop the rest.
	first := s.Store.Get(ids[0])
	s.Pool.CloseDocument(first.Doc)
	first.Doc = doc
	s.Store.Bump(first, pages, int64(len(data)))
	s.Thumbs.InvalidateVersion(first.Version - 1)
	for _, id := range ids[1:] {
		if other := s.Store.Delete(id); other != nil {
			s.Pool.CloseDocument(other.Doc)
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"id": first.ID, "name": first.Name, "pages": first.Pages, "version": first.Version})
}

// ---- security / decoration ---------------------------------------------

func (s *Server) watermark(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	var wRatio, hRatio float64
	var pos string
	var img *image.RGBA

	ctype := r.Header.Get("Content-Type")
	if strings.HasPrefix(ctype, "multipart/form-data") {
		r.Body = http.MaxBytesReader(w, r.Body, imageUploadMax+(1<<20))
		if err := r.ParseMultipartForm(uploadMemBytes); err != nil {
			writeErr(w, http.StatusBadRequest, "bad multipart: "+err.Error())
			return
		}
		defer func() {
			if r.MultipartForm != nil {
				_ = r.MultipartForm.RemoveAll()
			}
		}()
		file, hdr, err := r.FormFile("image")
		if err != nil || hdr == nil {
			writeErr(w, http.StatusBadRequest, "image file required")
			return
		}
		defer file.Close()
		if hdr.Size > imageUploadMax {
			writeErr(w, http.StatusRequestEntityTooLarge, "image too large")
			return
		}
		raw, err := io.ReadAll(io.LimitReader(file, imageUploadMax+1))
		if err != nil {
			writeErr(w, http.StatusBadRequest, "read error: "+err.Error())
			return
		}
		// Check dimensions before the full decode so a small crafted file
		// cannot expand into a multi-gigabyte bitmap.
		cfg, _, err := image.DecodeConfig(bytes.NewReader(raw))
		if err != nil {
			writeErr(w, http.StatusBadRequest, "cannot decode image: "+err.Error())
			return
		}
		if cfg.Width > watermarkPxMax || cfg.Height > watermarkPxMax {
			writeErr(w, http.StatusBadRequest, "image exceeds 4096x4096 pixels")
			return
		}
		decImg, _, derr := image.Decode(bytes.NewReader(raw))
		if derr != nil {
			writeErr(w, http.StatusBadRequest, "cannot decode image: "+derr.Error())
			return
		}
		b := decImg.Bounds()
		rgba := image.NewRGBA(image.Rect(0, 0, b.Dx(), b.Dy()))
		for y := b.Min.Y; y < b.Max.Y; y++ {
			for x := b.Min.X; x < b.Max.X; x++ {
				rgba.Set(x, y, decImg.At(x, y))
			}
		}
		img = rgba
		wRatio, _ = strconv.ParseFloat(r.FormValue("w"), 64)
		hRatio, _ = strconv.ParseFloat(r.FormValue("h"), 64)
		pos = r.FormValue("pos")
	} else {
		var body struct {
			Text   string  `json:"text"`
			WRatio float64 `json:"wRatio"`
			HRatio float64 `json:"hRatio"`
			Pos    string  `json:"pos"`
		}
		if err := decodeBody(w, r, &body); err != nil || body.Text == "" {
			writeErr(w, http.StatusBadRequest, "image or text required")
			return
		}
		wRatio = body.WRatio
		hRatio = body.HRatio
		pos = body.Pos
		var err error
		img, err = renderTextImage(body.Text, 64)
		if err != nil {
			writeErr(w, http.StatusInternalServerError, "watermark text: "+err.Error())
			return
		}
		if img == nil {
			writeErr(w, http.StatusInternalServerError, "no font available for text watermark")
			return
		}
	}
	if wRatio <= 0 {
		wRatio = 0.5
	}
	if hRatio <= 0 {
		hRatio = 0.1
	}
	if pos == "" {
		pos = "center"
	}
	data, pages, err := ops.Watermark(s.Pool, ses.Doc, img, wRatio, hRatio, pos)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := s.apply(ses, data, pages, ses.Password); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	s.respondEdit(w, ses)
}

func (s *Server) redact(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	var body struct {
		// Rects in normalized page coords (0..1, origin top-left).
		Rects []struct {
			Page float64 `json:"page"`
			X    float64 `json:"x"`
			Y    float64 `json:"y"`
			W    float64 `json:"w"`
			H    float64 `json:"h"`
		} `json:"rects"`
	}
	if err := decodeBody(w, r, &body); err != nil || len(body.Rects) == 0 {
		writeErr(w, http.StatusBadRequest, "rects required")
		return
	}
	rects := make([]pdfiumx.Rect, 0, len(body.Rects))
	for _, rr := range body.Rects {
		pg := int(rr.Page)
		if pg < 0 || pg >= ses.Pages {
			writeErr(w, http.StatusBadRequest, "page out of range")
			return
		}
		pw, ph, err := s.Pool.PageSize(ses.Doc, pg)
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		// Convert top-left normalized -> bottom-left points.
		x := rr.X * pw
		y := (1 - rr.Y - rr.H) * ph
		w := rr.W * pw
		h := rr.H * ph
		rects = append(rects, pdfiumx.Rect{Page: pg, X: x, Y: y, W: w, H: h})
	}
	data, pages, err := ops.Redact(s.Pool, ses.Doc, rects)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := s.apply(ses, data, pages, ses.Password); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	s.respondEdit(w, ses)
}

func (s *Server) protect(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	var body struct {
		User  string `json:"user"`
		Owner string `json:"owner"`
	}
	if err := decodeBody(w, r, &body); err != nil || body.User == "" {
		writeErr(w, http.StatusBadRequest, "user password required")
		return
	}
	data, err := s.currentPlain(ses)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	out, err := ops.Protect(data, body.User, body.Owner)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := s.apply(ses, out, ses.Pages, body.User); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	s.respondEdit(w, ses)
}

func (s *Server) unlock(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	var body struct {
		Password string `json:"password"`
	}
	if err := decodeBody(w, r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, "password required")
		return
	}
	data, err := s.currentBytes(ses)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	out, err := ops.Unlock(data, body.Password)
	if err != nil {
		writeErr(w, http.StatusBadRequest, "unlock failed: "+err.Error())
		return
	}
	if err := s.apply(ses, out, ses.Pages, ""); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	s.respondEdit(w, ses)
}

func (s *Server) compress(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	var body struct {
		Level int `json:"level"`
	}
	_ = decodeBody(w, r, &body)
	data, err := s.currentPlain(ses)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	out, err := ops.Compress(data, body.Level)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := s.apply(ses, out, ses.Pages, ses.Password); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	s.respondEdit(w, ses)
}

// currentBytes serializes the session's live document.
func (s *Server) currentBytes(ses *store.Session) ([]byte, error) {
	return s.Pool.Save(ses.Doc)
}

// currentPlain returns the current document state as plaintext. If the session
// is encrypted it is decrypted first so Ghostscript never sees an encrypted
// stream.
func (s *Server) currentPlain(ses *store.Session) ([]byte, error) {
	if ses.Password == "" {
		return s.Pool.Save(ses.Doc)
	}
	enc, err := s.Pool.Save(ses.Doc)
	if err != nil {
		return nil, err
	}
	return ops.Unlock(enc, ses.Password)
}

// respondEdit returns the post-edit session summary.
func (s *Server) respondEdit(w http.ResponseWriter, ses *store.Session) {
	writeJSON(w, http.StatusOK, map[string]any{
		"id":      ses.ID,
		"name":    ses.Name,
		"pages":   ses.Pages,
		"size":    ses.Size,
		"version": ses.Version,
	})
}

// ---- export -------------------------------------------------------------

func (s *Server) download(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	data, err := s.Pool.Save(ses.Doc)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	name := disposition(ses.Name, "-edited.pdf")
	w.Header().Set("Content-Type", "application/pdf")
	w.Header().Set("Content-Disposition", name)
	_, _ = w.Write(data)
}

func (s *Server) exportText(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	text, err := ops.Text(s.Pool, ses.Doc, nil)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Content-Disposition", disposition(ses.Name, ".txt"))
	_, _ = io.WriteString(w, text)
}

func (s *Server) exportImages(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	width := clampInt(queryInt(r, "w", 1100), 32, fullWidthMax)
	quality := clampInt(queryInt(r, "q", 85), 1, 95)
	jpeg := r.URL.Query().Get("fmt") == "jpg"
	images, err := ops.ExportImages(s.Pool, ses.Doc, nil, width, quality, jpeg)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	// Return a JSON manifest of data URLs so the SPA can offer downloads
	// without a zip dependency.
	out := map[string]string{}
	for name, b := range images {
		enc := "png"
		if jpeg {
			enc = "jpeg"
		}
		out[name] = "data:image/" + enc + ";base64," + b64(b)
	}
	writeJSON(w, http.StatusOK, out)
}

// ---- static -------------------------------------------------------------

// spaHandler serves the embedded SPA. Existing assets are served directly;
// anything else (client routes, root) falls back to index.html.
func spaHandler(fsys fs.FS) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.Path, "/")
		if path == "" {
			path = "index.html"
		}
		data, err := fs.ReadFile(fsys, path)
		if err != nil {
			// Client route -> SPA shell.
			data, err = fs.ReadFile(fsys, "index.html")
			if err != nil {
				http.Error(w, "not found", http.StatusNotFound)
				return
			}
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			_, _ = w.Write(data)
			return
		}
		w.Header().Set("Content-Type", contentType(path))
		_, _ = w.Write(data)
	})
}

func contentType(path string) string {
	switch {
	case strings.HasSuffix(path, ".js"):
		return "text/javascript; charset=utf-8"
	case strings.HasSuffix(path, ".css"):
		return "text/css; charset=utf-8"
	case strings.HasSuffix(path, ".html"):
		return "text/html; charset=utf-8"
	case strings.HasSuffix(path, ".png"):
		return "image/png"
	case strings.HasSuffix(path, ".svg"):
		return "image/svg+xml"
	case strings.HasSuffix(path, ".ico"):
		return "image/x-icon"
	default:
		return "application/octet-stream"
	}
}

func queryInt(r *http.Request, key string, def int) int {
	if v := r.URL.Query().Get(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func b64(b []byte) string {
	return base64.StdEncoding.EncodeToString(b)
}

var _ = errors.New
var _ = time.Now
