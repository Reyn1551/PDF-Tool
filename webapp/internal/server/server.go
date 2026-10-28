// Package server is the HTTP layer: JSON APIs for every operation plus static
// SPA serving. It keeps one open PDFium document per session and a byte-budgeted
// thumbnail cache, and re-opens the document after each edit so the handle
// always matches the current bytes.
package server

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"io"
	"io/fs"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"pdftoolweb/internal/ops"
	"pdftoolweb/internal/pdfiumx"
	"pdftoolweb/internal/store"
)

// Server holds shared state.
type Server struct {
	Pool  *pdfiumx.Pool
	Store *store.Store
	Thumbs *store.ThumbCache
	// MaxUpload is the largest accepted PDF in bytes.
	MaxUpload int64
	// Static is the embedded web assets (index.html, app.js, style.css).
	Static fs.FS
}

// New builds a Server.
func New(pool *pdfiumx.Pool, static fs.FS) *Server {
	return &Server{
		Pool:      pool,
		Store:     store.NewStore(),
		Thumbs:    store.NewThumbCache(64 << 20), // 64 MB thumbnail budget
		MaxUpload: 150 << 20,                     // 150 MB
		Static:    static,
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

	return withCORS(withRecover(mux))
}

// ---- middleware ---------------------------------------------------------

func withCORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
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
	if err := r.ParseMultipartForm(s.MaxUpload); err != nil {
		writeErr(w, http.StatusBadRequest, "upload too large or malformed: "+err.Error())
		return
	}
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
	data, err := io.ReadAll(file)
	if err != nil {
		writeErr(w, http.StatusBadRequest, "read error: "+err.Error())
		return
	}
	name := hdr.Filename
	if name == "" {
		name = "document.pdf"
	}
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
		"id":        ses.ID,
		"name":      ses.Name,
		"pages":     ses.Pages,
		"size":      ses.Size,
		"version":   ses.Version,
		"metadata":  meta,
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
	width := queryInt(r, "w", 140)
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
	width := queryInt(r, "w", 1100)
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
	docs := make([]ops.Doc, 0, len(body.IDs))
	for _, id := range body.IDs {
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
	first := s.Store.Get(body.IDs[0])
	s.Pool.CloseDocument(first.Doc)
	first.Doc = doc
	s.Store.Bump(first, pages, int64(len(data)))
	s.Thumbs.InvalidateVersion(first.Version - 1)
	for _, id := range body.IDs[1:] {
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
		if err := r.ParseMultipartForm(s.MaxUpload); err != nil {
			writeErr(w, http.StatusBadRequest, "bad multipart: "+err.Error())
			return
		}
		file, hdr, err := r.FormFile("image")
		if err != nil || hdr == nil {
			writeErr(w, http.StatusBadRequest, "image file required")
			return
		}
		defer file.Close()
		decImg, _, derr := image.Decode(file)
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
			Text     string  `json:"text"`
			WRatio   float64 `json:"wRatio"`
			HRatio   float64 `json:"hRatio"`
			Pos      string  `json:"pos"`
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
	name := strings.TrimSuffix(ses.Name, ".pdf") + "-edited.pdf"
	w.Header().Set("Content-Type", "application/pdf")
	w.Header().Set("Content-Disposition", `attachment; filename="`+name+`"`)
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
	w.Header().Set("Content-Disposition", `attachment; filename="`+strings.TrimSuffix(ses.Name, ".pdf")+".txt" + `"`)
	_, _ = io.WriteString(w, text)
}

func (s *Server) exportImages(w http.ResponseWriter, r *http.Request) {
	ses, ok := s.getSession(w, r.PathValue("id"))
	if !ok {
		return
	}
	width := queryInt(r, "w", 1100)
	quality := queryInt(r, "q", 85)
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
