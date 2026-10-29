package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"pdftoolweb/internal/pdfiumx"
	"pdftoolweb/internal/store"
)

func TestRateLimiter(t *testing.T) {
	l := newRateLimiter()
	for i := 0; i < 3; i++ {
		if !l.allow("1.2.3.4", 3) {
			t.Fatalf("request %d unexpectedly limited", i+1)
		}
	}
	if l.allow("1.2.3.4", 3) {
		t.Fatal("4th request should be limited")
	}
	if !l.allow("5.6.7.8", 3) {
		t.Fatal("other key must not be limited")
	}
	if !l.allow("1.2.3.4", 0) {
		t.Fatal("limit 0 means unlimited")
	}
}

func TestClientIPTrustedOnlyFromLoopback(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/health", nil)
	req.RemoteAddr = "203.0.113.9:1234"
	req.Header.Set("X-Real-IP", "10.0.0.1")
	if ip := clientIP(req); ip != "203.0.113.9" {
		t.Fatalf("spoofed X-Real-IP trusted: %s", ip)
	}
	req.RemoteAddr = "127.0.0.1:5678"
	if ip := clientIP(req); ip != "10.0.0.1" {
		t.Fatalf("loopback proxy X-Real-IP ignored: %s", ip)
	}
}

func TestSanitizeName(t *testing.T) {
	cases := map[string]string{
		"":                   "document.pdf",
		"report.pdf":         "report.pdf",
		`..\..\evil".pdf`:    "evil.pdf",
		"a\r\nb.pdf":         "ab.pdf",
		"/etc/passwd":        "passwd",
		`C:\tmp\secret".pdf`: "secret.pdf",
	}
	for in, want := range cases {
		if got := sanitizeName(in); got != want {
			t.Errorf("sanitizeName(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestStoreSweep(t *testing.T) {
	pool, err := pdfiumx.NewPool()
	if err != nil {
		t.Fatalf("pool: %v", err)
	}
	t.Cleanup(func() { pool.Close() })
	doc, err := pool.NewDocument()
	if err != nil {
		t.Fatalf("new doc: %v", err)
	}
	defer pool.CloseDocument(doc)

	st := store.NewStore()
	ses := st.Add("a.pdf", doc, 1, 10)
	if n := st.Count(); n != 1 {
		t.Fatalf("count: %d", n)
	}
	if dropped := st.Sweep(time.Hour); len(dropped) != 0 {
		t.Fatalf("fresh session swept: %v", dropped)
	}
	// Force-expire by sweeping with a negative TTL.
	dropped := st.Sweep(-time.Second)
	if len(dropped) != 1 || dropped[0].ID != ses.ID {
		t.Fatalf("sweep: %v", dropped)
	}
	if n := st.Count(); n != 0 {
		t.Fatalf("count after sweep: %d", n)
	}
}
