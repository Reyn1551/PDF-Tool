package server_test

import (
	"bytes"
	"encoding/json"
	"image"
	"image/color"
	"image/png"
	"io"
	"io/fs"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"pdftoolweb/internal/pdfiumx"
	"pdftoolweb/internal/server"
)

// makeTestPDF builds a small N-page PDF in a temp dir using the same PDFium
// pool the server uses, and returns its path.
func makeTestPDF(t *testing.T, pool *pdfiumx.Pool, pages int) string {
	t.Helper()
	doc, err := pool.NewDocument()
	if err != nil {
		t.Fatalf("new doc: %v", err)
	}
	defer pool.CloseDocument(doc)
	for i := 0; i < pages; i++ {
		if err := pool.NewPage(doc, i, 200, 260); err != nil {
			t.Fatalf("new page: %v", err)
		}
	}
	data, err := pool.Save(doc)
	if err != nil {
		t.Fatalf("save: %v", err)
	}
	path := filepath.Join(t.TempDir(), "test.pdf")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	return path
}

// whitePNG returns a small opaque PNG with a colored square (for image
// watermark tests).
func whitePNG() []byte {
	img := image.NewRGBA(image.Rect(0, 0, 40, 20))
	for y := 0; y < 20; y++ {
		for x := 0; x < 40; x++ {
			img.Set(x, y, color.RGBA{R: 200, G: 0, B: 0, A: 255})
		}
	}
	var b bytes.Buffer
	_ = png.Encode(&b, img)
	return b.Bytes()
}

// startServer boots a real server on a random port.
func startServer(t *testing.T) (*httptest.Server, *pdfiumx.Pool, string) {
	t.Helper()
	pool, err := pdfiumx.NewPool()
	if err != nil {
		t.Fatalf("pool: %v", err)
	}
	t.Cleanup(func() { pool.Close() })

	// Serve the embedded web assets if present, else an empty FS.
	var static = emptyFS{}
	srv := server.New(pool, static)
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)
	pdfPath := makeTestPDF(t, pool, 5)
	return ts, pool, pdfPath
}

// emptyFS is a minimal fs.FS for tests (no assets needed for API tests).
type emptyFS struct{}

func (emptyFS) Open(string) (fs.File, error) { return nil, os.ErrNotExist }

// ---- helpers -----------------------------------------------------------

func upload(t *testing.T, base, path string) map[string]any {
	t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	f, _ := os.Open(path)
	defer f.Close()
	parts, _ := mw.CreateFormFile("file", filepath.Base(path))
	_, _ = io.Copy(parts, f)
	mw.Close()
	resp := do(t, http.MethodPost, base+"/api/upload", &buf, "multipart/form-data; boundary="+mw.Boundary())
	return mustJSON(t, resp)
}

func do(t *testing.T, method, url string, body *bytes.Buffer, ctype string) *http.Response {
	t.Helper()
	var rd *bytes.Reader
	if body != nil {
		rd = bytes.NewReader(body.Bytes())
	} else {
		rd = bytes.NewReader(nil)
	}
	req, _ := http.NewRequest(method, url, rd)
	if ctype != "" {
		req.Header.Set("Content-Type", ctype)
	}
	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, url, err)
	}
	return resp
}

func post(t *testing.T, url string, v any) *http.Response {
	b, _ := json.Marshal(v)
	return do(t, http.MethodPost, url, bytes.NewBuffer(b), "application/json")
}

func get(t *testing.T, url string) *http.Response {
	return do(t, http.MethodGet, url, nil, "")
}

func mustJSON(t *testing.T, resp *http.Response) map[string]any {
	t.Helper()
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		var e map[string]string
		_ = json.NewDecoder(resp.Body).Decode(&e)
		t.Fatalf("unexpected status %d for %s: %v", resp.StatusCode, resp.Request.URL, e)
	}
	var m map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&m); err != nil {
		t.Fatalf("decode: %v", err)
	}
	return m
}

func idOf(m map[string]any) string { return m["id"].(string) }
func pagesOf(m map[string]any) int { return int(m["pages"].(float64)) }

// ---- tests -------------------------------------------------------------

func TestHealth(t *testing.T) {
	ts, _, _ := startServer(t)
	resp := get(t, ts.URL+"/api/health")
	m := mustJSON(t, resp)
	if m["ok"] != true {
		t.Fatalf("health not ok: %v", m)
	}
}

func TestUploadAndRead(t *testing.T) {
	ts, _, pdf := startServer(t)
	up := upload(t, ts.URL, pdf)
	id := idOf(up)
	if pagesOf(up) != 5 {
		t.Fatalf("expected 5 pages, got %v", up["pages"])
	}
	// info
	info := mustJSON(t, get(t, ts.URL+"/api/session/"+id))
	if pagesOf(info) != 5 {
		t.Fatalf("info pages: %v", info["pages"])
	}
	// thumbnail
	resp := get(t, ts.URL+"/api/session/"+id+"/page/0/thumb?w=100")
	defer resp.Body.Close()
	if ct := resp.Header.Get("Content-Type"); ct != "image/png" {
		t.Fatalf("thumb content-type: %s", ct)
	}
	buf := new(bytes.Buffer)
	_, _ = buf.ReadFrom(resp.Body)
	if !bytes.HasPrefix(buf.Bytes(), []byte("\x89PNG")) {
		t.Fatalf("thumb not PNG")
	}
	// full page
	fresp := get(t, ts.URL+"/api/session/"+id+"/page/1/full?w=400")
	defer fresp.Body.Close()
	if fresp.Header.Get("Content-Type") != "image/png" {
		t.Fatalf("full content-type: %s", fresp.Header.Get("Content-Type"))
	}
	// metadata
	meta := mustJSON(t, get(t, ts.URL+"/api/session/"+id+"/metadata"))
	if meta == nil {
		t.Fatal("metadata empty")
	}
}

func TestOrganizeOps(t *testing.T) {
	ts, _, pdf := startServer(t)
	id := idOf(upload(t, ts.URL, pdf))
	base := ts.URL + "/api/session/" + id + "/"

	// rotate page 0
	r := mustJSON(t, post(t, base+"rotate", map[string]any{"pages": []int{0}, "deg": 90}))
	if pagesOf(r) != 5 {
		t.Fatalf("rotate pages: %v", r["pages"])
	}
	if v := int(r["version"].(float64)); v != 2 {
		t.Fatalf("version after rotate: %v", v)
	}

	// delete page 4
	d := mustJSON(t, post(t, base+"delete", map[string]any{"pages": []int{4}}))
	if pagesOf(d) != 4 {
		t.Fatalf("delete pages: %v", d["pages"])
	}

	// reverse
	rv := mustJSON(t, post(t, base+"reverse", map[string]any{}))
	if pagesOf(rv) != 4 {
		t.Fatalf("reverse pages: %v", rv["pages"])
	}

	// reorder identity
	ro := mustJSON(t, post(t, base+"reorder", map[string]any{"order": []int{0, 1, 2, 3}}))
	if pagesOf(ro) != 4 {
		t.Fatalf("reorder pages: %v", ro["pages"])
	}

	// split 1-2 -> new session
	sp := mustJSON(t, post(t, base+"split", map[string]any{"start": 1, "end": 2}))
	if pagesOf(sp) != 2 {
		t.Fatalf("split pages: %v", sp["pages"])
	}
	if idOf(sp) == id {
		t.Fatal("split should create a new session")
	}
}

func TestMerge(t *testing.T) {
	ts, _, pdf := startServer(t)
	a := idOf(upload(t, ts.URL, pdf))
	b := idOf(upload(t, ts.URL, pdf))
	m := mustJSON(t, post(t, ts.URL+"/api/session/"+a+"/merge", map[string]any{"ids": []string{a, b}}))
	if pagesOf(m) != 10 {
		t.Fatalf("merge pages: %v (want 10)", m["pages"])
	}
}

func TestWatermarkText(t *testing.T) {
	ts, _, pdf := startServer(t)
	id := idOf(upload(t, ts.URL, pdf))
	w := mustJSON(t, post(t, ts.URL+"/api/session/"+id+"/watermark",
		map[string]any{"text": "CONFIDENTIAL", "wRatio": 0.5, "hRatio": 0.1, "pos": "center"}))
	if _, ok := w["error"]; ok {
		t.Fatalf("watermark failed: %v", w["error"])
	}
	if pagesOf(w) != 5 {
		t.Fatalf("watermark pages: %v", w["pages"])
	}
}

func TestWatermarkImage(t *testing.T) {
	ts, _, pdf := startServer(t)
	id := idOf(upload(t, ts.URL, pdf))
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, _ := mw.CreateFormFile("image", "mark.png")
	_, _ = fw.Write(whitePNG())
	mw.WriteField("w", "0.5")
	mw.WriteField("h", "0.1")
	mw.WriteField("pos", "center")
	mw.Close()
	resp := do(t, http.MethodPost, ts.URL+"/api/session/"+id+"/watermark", &buf,
		"multipart/form-data; boundary="+mw.Boundary())
	m := mustJSON(t, resp)
	if _, ok := m["error"]; ok {
		t.Fatalf("image watermark failed: %v", m["error"])
	}
}

func TestRedact(t *testing.T) {
	ts, _, pdf := startServer(t)
	id := idOf(upload(t, ts.URL, pdf))
	r := mustJSON(t, post(t, ts.URL+"/api/session/"+id+"/redact",
		map[string]any{"rects": []map[string]any{{"page": 0, "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.1}}}))
	if _, ok := r["error"]; ok {
		t.Fatalf("redact failed: %v", r["error"])
	}
}

func TestCompress(t *testing.T) {
	if _, err := exec.LookPath("gs"); err != nil {
		t.Skip("ghostscript not installed")
	}
	ts, _, pdf := startServer(t)
	id := idOf(upload(t, ts.URL, pdf))
	c := mustJSON(t, post(t, ts.URL+"/api/session/"+id+"/compress", map[string]any{"level": 0}))
	if _, ok := c["error"]; ok {
		t.Fatalf("compress failed: %v", c["error"])
	}
	if pagesOf(c) != 5 {
		t.Fatalf("compress pages: %v", c["pages"])
	}
}

func TestProtectUnlock(t *testing.T) {
	if _, err := exec.LookPath("gs"); err != nil {
		t.Skip("ghostscript not installed")
	}
	ts, pool, pdf := startServer(t)
	id := idOf(upload(t, ts.URL, pdf))
	base := ts.URL + "/api/session/" + id + "/"

	p := mustJSON(t, post(t, base+"protect", map[string]any{"user": "secret", "owner": "owner"}))
	if _, ok := p["error"]; ok {
		t.Fatalf("protect failed: %v", p["error"])
	}
	// The downloaded file must be genuinely encrypted: opening it without the
	// password must fail.
	dresp := get(t, base+"download")
	defer dresp.Body.Close()
	var enc bytes.Buffer
	_, _ = enc.ReadFrom(dresp.Body)
	if _, err := pool.Open(enc.Bytes(), ""); err == nil {
		t.Fatalf("protected file opened without password — not encrypted")
	}
	// ...but with the password it opens.
	pdoc, err := pool.Open(enc.Bytes(), "secret")
	if err != nil {
		t.Fatalf("protected file failed to open with password: %v", err)
	}
	pool.CloseDocument(pdoc)
	// still readable (server holds the password)
	txt := mustJSON(t, get(t, base+"page/0/text"))
	if txt["page"] != float64(0) {
		t.Fatalf("text after protect: %v", txt)
	}
	// compress while encrypted (decrypt -> gs -> re-encrypt)
	c := mustJSON(t, post(t, base+"compress", map[string]any{"level": 1}))
	if _, ok := c["error"]; ok {
		t.Fatalf("compress-while-encrypted failed: %v", c["error"])
	}
	// unlock
	u := mustJSON(t, post(t, base+"unlock", map[string]any{"password": "secret"}))
	if _, ok := u["error"]; ok {
		t.Fatalf("unlock failed: %v", u["error"])
	}
}

func TestExportText(t *testing.T) {
	ts, _, pdf := startServer(t)
	id := idOf(upload(t, ts.URL, pdf))
	resp := get(t, ts.URL+"/api/session/"+id+"/export/text")
	defer resp.Body.Close()
	if resp.Header.Get("Content-Type") != "text/plain; charset=utf-8" {
		t.Fatalf("export text content-type: %s", resp.Header.Get("Content-Type"))
	}
}

func TestDownload(t *testing.T) {
	ts, _, pdf := startServer(t)
	id := idOf(upload(t, ts.URL, pdf))
	resp := get(t, ts.URL+"/api/session/"+id+"/download")
	defer resp.Body.Close()
	if resp.Header.Get("Content-Type") != "application/pdf" {
		t.Fatalf("download content-type: %s", resp.Header.Get("Content-Type"))
	}
	buf := new(bytes.Buffer)
	_, _ = buf.ReadFrom(resp.Body)
	if !bytes.HasPrefix(buf.Bytes(), []byte("%PDF")) {
		t.Fatalf("download not a PDF")
	}
	if cd := resp.Header.Get("Content-Disposition"); !strings.Contains(cd, "attachment") {
		t.Fatalf("download disposition: %s", cd)
	}
}

func TestSessionNotFound(t *testing.T) {
	ts, _, _ := startServer(t)
	resp := get(t, ts.URL+"/api/session/does-not-exist")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", resp.StatusCode)
	}
}
