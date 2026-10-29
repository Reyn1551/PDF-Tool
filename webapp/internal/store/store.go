// Package store holds open PDF sessions and a bounded thumbnail cache.
//
// Memory model for a 1 GB box:
//   - one PDFium context total (see pdfiumx); a handful of open documents;
//   - thumbnails are the only unbounded artifact, so they live in an LRU with
//     a hard byte budget and are evicted as new pages render.
package store

import (
	"container/list"
	"crypto/rand"
	"encoding/hex"
	"sync"
	"time"

	"github.com/klippa-app/go-pdfium/references"
)

// Session is one open PDF document.
type Session struct {
	ID    string
	Name  string
	Doc   references.FPDF_DOCUMENT
	Pages int
	// Version increments on every edit so the thumbnail cache and the client
	// can invalidate stale bitmaps.
	Version int
	// Size is the byte length of the current (possibly edited) document.
	Size int64
	// Password is set when the document is encrypted, so it can be re-opened
	// after an edit.
	Password string
	// lastSeen is bumped on every access; idle sessions are swept so an
	// abandoned tab cannot pin memory forever on a public instance.
	lastSeen time.Time
}

// Store owns all sessions.
type Store struct {
	mu  sync.Mutex
	ses map[string]*Session
}

// NewStore returns an empty store.
func NewStore() *Store {
	return &Store{ses: map[string]*Session{}}
}

// Add registers a freshly opened document.
func (s *Store) Add(name string, doc references.FPDF_DOCUMENT, pages int, size int64) *Session {
	s.mu.Lock()
	defer s.mu.Unlock()
	id := newID()
	ses := &Session{ID: id, Name: name, Doc: doc, Pages: pages, Size: size, Version: 1, lastSeen: time.Now()}
	s.ses[id] = ses
	return ses
}

// Get returns a session or nil, marking it as recently used.
func (s *Store) Get(id string) *Session {
	s.mu.Lock()
	defer s.mu.Unlock()
	ses := s.ses[id]
	if ses != nil {
		ses.lastSeen = time.Now()
	}
	return ses
}

// Count reports how many sessions are open.
func (s *Store) Count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.ses)
}

// Sweep removes sessions idle longer than ttl and returns them so the caller
// can close their PDFium documents.
func (s *Store) Sweep(ttl time.Duration) []*Session {
	s.mu.Lock()
	defer s.mu.Unlock()
	cutoff := time.Now().Add(-ttl)
	var dropped []*Session
	for id, ses := range s.ses {
		if ses.lastSeen.Before(cutoff) {
			delete(s.ses, id)
			dropped = append(dropped, ses)
		}
	}
	return dropped
}

// All returns a snapshot of every session id.
func (s *Store) All() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	ids := make([]string, 0, len(s.ses))
	for id := range s.ses {
		ids = append(ids, id)
	}
	return ids
}

// Delete removes a session (the caller closes the PDFium doc).
func (s *Store) Delete(id string) *Session {
	s.mu.Lock()
	defer s.mu.Unlock()
	ses := s.ses[id]
	if ses != nil {
		delete(s.ses, id)
	}
	return ses
}

// Bump marks a session as edited.
func (s *Store) Bump(ses *Session, pages int, size int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	ses.Version++
	ses.Pages = pages
	ses.Size = size
	ses.lastSeen = time.Now()
}

func newID() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// ---- thumbnail cache ----------------------------------------------------

type thumbKey struct {
	version, page, width int
}

type thumbEntry struct {
	key  thumbKey
	data []byte
}

// ThumbCache is a byte-budgeted LRU of rendered page thumbnails.
type ThumbCache struct {
	mu     sync.Mutex
	budget int64
	used   int64
	ll     *list.List
	index  map[thumbKey]*list.Element
}

// NewThumbCache creates a cache that holds at most budget bytes.
func NewThumbCache(budget int64) *ThumbCache {
	if budget < 1<<20 {
		budget = 1 << 20
	}
	return &ThumbCache{
		budget: budget,
		ll:     list.New(),
		index:  map[thumbKey]*list.Element{},
	}
}

// Get returns cached thumbnail bytes and true on a hit.
func (c *ThumbCache) Get(version, page, width int) ([]byte, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	el, ok := c.index[thumbKey{version, page, width}]
	if !ok {
		return nil, false
	}
	c.ll.MoveToFront(el)
	return el.Value.(*thumbEntry).data, true
}

// Put stores thumbnail bytes, evicting oldest entries past the budget.
func (c *ThumbCache) Put(version, page, width int, data []byte) {
	c.mu.Lock()
	defer c.mu.Unlock()
	k := thumbKey{version, page, width}
	if el, ok := c.index[k]; ok {
		c.used -= int64(len(el.Value.(*thumbEntry).data))
		c.ll.Remove(el)
		delete(c.index, k)
	}
	entry := &thumbEntry{key: k, data: data}
	el := c.ll.PushFront(entry)
	c.index[k] = el
	c.used += int64(len(data))
	for c.used > c.budget && c.ll.Len() > 0 {
		back := c.ll.Back()
		e := back.Value.(*thumbEntry)
		c.ll.Remove(back)
		delete(c.index, e.key)
		c.used -= int64(len(e.data))
	}
}

// InvalidateVersion drops every thumbnail for a document version (after an
// edit the old bitmaps are stale).
func (c *ThumbCache) InvalidateVersion(version int) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for k, el := range c.index {
		if k.version == version {
			c.used -= int64(len(el.Value.(*thumbEntry).data))
			c.ll.Remove(el)
			delete(c.index, k)
		}
	}
}

// Stats reports cache usage for /api/health.
func (c *ThumbCache) Stats() (entries int, used int64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.ll.Len(), c.used
}
