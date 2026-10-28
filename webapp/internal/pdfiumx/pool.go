// Package pdfiumx wraps go-pdfium with a single serialized worker.
//
// PDFium is not safe for concurrent use of a single context, and the target
// deployment is a 1 vCPU box, so all calls are funneled through one instance
// guarded by a mutex. Serializing is correct there (there is no second core to
// overlap on) and it keeps memory flat: one PDFium context, one open document
// at a time.
package pdfiumx

import (
	"bytes"
	"fmt"
	"image"
	"image/jpeg"
	"image/png"
	"sync"
	"time"

	"github.com/klippa-app/go-pdfium"
	"github.com/klippa-app/go-pdfium/enums"
	"github.com/klippa-app/go-pdfium/requests"
	"github.com/klippa-app/go-pdfium/references"
	"github.com/klippa-app/go-pdfium/single_threaded"
	"github.com/klippa-app/go-pdfium/structs"
)

// Pool owns the single PDFium instance.
type Pool struct {
	mu   sync.Mutex
	pool pdfium.Pool
	inst pdfium.Pdfium
}

// NewPool starts the single-threaded PDFium pool and claims the one instance.
func NewPool() (*Pool, error) {
	p := single_threaded.Init(single_threaded.Config{})
	inst, err := p.GetInstance(30 * time.Second)
	if err != nil {
		p.Close()
		return nil, err
	}
	return &Pool{pool: p, inst: inst}, nil
}

// Close releases the instance and pool.
func (p *Pool) Close() error {
	if p.inst != nil {
		p.inst.Close()
		p.inst = nil
	}
	return p.pool.Close()
}

// With runs fn with the single instance, serialized.
func (p *Pool) With(fn func(i pdfium.Pdfium) error) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.inst == nil {
		return fmt.Errorf("pdfium pool is closed")
	}
	return fn(p.inst)
}

// PageByIndex builds a page selector.
func PageByIndex(doc references.FPDF_DOCUMENT, index int) requests.Page {
	return requests.Page{ByIndex: &requests.PageByIndex{Document: doc, Index: index}}
}

// Doc is an open PDFium document handle.
type Doc = references.FPDF_DOCUMENT

// ---- document lifecycle -------------------------------------------------

// Open opens a document from bytes and returns its opaque handle.
func (p *Pool) Open(data []byte, password string) (references.FPDF_DOCUMENT, error) {
	var doc references.FPDF_DOCUMENT
	err := p.With(func(i pdfium.Pdfium) error {
		req := &requests.OpenDocument{File: &data}
		if password != "" {
			pw := password
			req.Password = &pw
		}
		resp, err := i.OpenDocument(req)
		if err != nil {
			return err
		}
		doc = resp.Document
		return nil
	})
	return doc, err
}

// CloseDocument closes a document handle.
func (p *Pool) CloseDocument(doc references.FPDF_DOCUMENT) {
	_ = p.With(func(i pdfium.Pdfium) error {
		_, _ = i.FPDF_CloseDocument(&requests.FPDF_CloseDocument{Document: doc})
		return nil
	})
}

// PageCount returns the number of pages.
func (p *Pool) PageCount(doc references.FPDF_DOCUMENT) (int, error) {
	var n int
	err := p.With(func(i pdfium.Pdfium) error {
		resp, err := i.FPDF_GetPageCount(&requests.FPDF_GetPageCount{Document: doc})
		if err != nil {
			return err
		}
		n = resp.PageCount
		return nil
	})
	return n, err
}

// PageSize returns the page size in points.
func (p *Pool) PageSize(doc references.FPDF_DOCUMENT, index int) (w, h float64, err error) {
	err = p.With(func(i pdfium.Pdfium) error {
		resp, err := i.FPDF_GetPageSizeByIndexF(&requests.FPDF_GetPageSizeByIndexF{
			Document: doc,
			Index:    index,
		})
		if err != nil {
			return err
		}
		w, h = float64(resp.Size.Width), float64(resp.Size.Height)
		return nil
	})
	return w, h, err
}

// PageRotation returns the current rotation of a page (0/90/180/270 CW).
func (p *Pool) PageRotation(doc references.FPDF_DOCUMENT, index int) (int, error) {
	var rot int
	err := p.With(func(i pdfium.Pdfium) error {
		resp, err := i.FPDFPage_GetRotation(&requests.FPDFPage_GetRotation{Page: PageByIndex(doc, index)})
		if err != nil {
			return err
		}
		rot = int(resp.PageRotation)
		return nil
	})
	return rot, err
}

// RenderPNG renders a page to PNG at the given target width (px). A width of
// 0 means "use height". Returns the PNG bytes and the rendered dimensions.
func (p *Pool) RenderPNG(doc references.FPDF_DOCUMENT, index, width, height int) ([]byte, int, int, error) {
	var pngBytes []byte
	var rw, rh int
	err := p.With(func(i pdfium.Pdfium) error {
		resp, err := i.RenderPageInPixels(&requests.RenderPageInPixels{
			Page:   PageByIndex(doc, index),
			Width:  width,
			Height: height,
		})
		if err != nil {
			return err
		}
		defer resp.Cleanup()
		var buf bytes.Buffer
		if err := png.Encode(&buf, resp.Result.RenderedImage); err != nil {
			return err
		}
		pngBytes = buf.Bytes()
		rw, rh = resp.Result.Width, resp.Result.Height
		return nil
	})
	return pngBytes, rw, rh, err
}

// RenderJPEG renders a page to JPEG at the given target width (px) and quality.
func (p *Pool) RenderJPEG(doc references.FPDF_DOCUMENT, index, width, quality int) ([]byte, error) {
	var out []byte
	err := p.With(func(i pdfium.Pdfium) error {
		resp, err := i.RenderPageInPixels(&requests.RenderPageInPixels{
			Page:   PageByIndex(doc, index),
			Width:  width,
			Height: 0,
		})
		if err != nil {
			return err
		}
		defer resp.Cleanup()
		img, ok := resp.Result.RenderedImage.(*image.RGBA)
		if !ok {
			return fmt.Errorf("render did not return RGBA")
		}
		var buf bytes.Buffer
		if err := jpeg.Encode(&buf, img, &jpeg.Options{Quality: quality}); err != nil {
			return err
		}
		out = buf.Bytes()
		return nil
	})
	return out, err
}

// PageText returns the plain text of a page.
func (p *Pool) PageText(doc references.FPDF_DOCUMENT, index int) (string, error) {
	var text string
	err := p.With(func(i pdfium.Pdfium) error {
		resp, err := i.GetPageText(&requests.GetPageText{Page: PageByIndex(doc, index)})
		if err != nil {
			return err
		}
		text = resp.Text
		return nil
	})
	return text, err
}

// Metadata returns the document info dictionary.
func (p *Pool) Metadata(doc references.FPDF_DOCUMENT) (map[string]string, error) {
	tags := []string{"Title", "Author", "Subject", "Keywords", "Creator", "Producer", "CreationDate", "ModDate"}
	var meta map[string]string
	err := p.With(func(i pdfium.Pdfium) error {
		meta = make(map[string]string, len(tags))
		for _, tag := range tags {
			resp, err := i.FPDF_GetMetaText(&requests.FPDF_GetMetaText{Document: doc, Tag: tag})
			if err != nil {
				continue
			}
			meta[tag] = resp.Value
		}
		return nil
	})
	return meta, err
}

// Save serializes a (possibly modified) document to bytes.
func (p *Pool) Save(doc references.FPDF_DOCUMENT) ([]byte, error) {
	var out []byte
	err := p.With(func(i pdfium.Pdfium) error {
		resp, err := i.FPDF_SaveAsCopy(&requests.FPDF_SaveAsCopy{
			Flags:    requests.SaveFlagNoIncremental,
			Document: doc,
		})
		if err != nil {
			return err
		}
		if resp.FileBytes == nil {
			return fmt.Errorf("save returned no bytes")
		}
		out = *resp.FileBytes
		return nil
	})
	return out, err
}

// ---- editing ------------------------------------------------------------

// ImportPages copies pages from src into dst at the given index.
func (p *Pool) ImportPages(dst, src references.FPDF_DOCUMENT, indices []int, at int) error {
	return p.With(func(i pdfium.Pdfium) error {
		_, err := i.FPDF_ImportPagesByIndex(&requests.FPDF_ImportPagesByIndex{
			Source:      src,
			Destination: dst,
			PageIndices: indices,
			Index:       at,
		})
		return err
	})
}

// MovePages reorders pages: the ordered list of page indices is moved to the
// destination position.
func (p *Pool) MovePages(doc references.FPDF_DOCUMENT, indices []int, dest int) error {
	return p.With(func(i pdfium.Pdfium) error {
		_, err := i.FPDF_MovePages(&requests.FPDF_MovePages{
			Document:      doc,
			PageIndices:   indices,
			DestPageIndex: dest,
		})
		return err
	})
}

// RotatePage sets the rotation of a page (0/90/180/270).
func (p *Pool) RotatePage(doc references.FPDF_DOCUMENT, index, deg int) error {
	var rot enums.FPDF_PAGE_ROTATION
	switch deg % 360 {
	case 90:
		rot = enums.FPDF_PAGE_ROTATION_90_CW
	case 180:
		rot = enums.FPDF_PAGE_ROTATION_180_CW
	case 270:
		rot = enums.FPDF_PAGE_ROTATION_270_CW
	default:
		rot = enums.FPDF_PAGE_ROTATION_NONE
	}
	return p.With(func(i pdfium.Pdfium) error {
		_, err := i.FPDFPage_SetRotation(&requests.FPDFPage_SetRotation{
			Page:   PageByIndex(doc, index),
			Rotate: rot,
		})
		return err
	})
}

// Redact paints opaque white rectangles over the given regions (page, x, y,
// w, h in points, origin bottom-left) and flattens each affected page so the
// marks become part of the page content. This is a white-out redaction: the
// covered area is visually and structurally baked in.
func (p *Pool) Redact(doc references.FPDF_DOCUMENT, rects []Rect) error {
	affected := map[int]bool{}
	return p.With(func(i pdfium.Pdfium) error {
		for _, r := range rects {
			affected[r.Page] = true
			obj, err := i.FPDFPageObj_CreateNewRect(&requests.FPDFPageObj_CreateNewRect{
				X: float32(r.X), Y: float32(r.Y), W: float32(r.W), H: float32(r.H),
			})
			if err != nil {
				return err
			}
			if _, err := i.FPDFPageObj_SetFillColor(&requests.FPDFPageObj_SetFillColor{
				PageObject: obj.PageObject,
				FillColor:  structs.FPDF_COLOR{R: 255, G: 255, B: 255, A: 255},
			}); err != nil {
				return err
			}
			if _, err := i.FPDFPage_InsertObject(&requests.FPDFPage_InsertObject{
				Page:       PageByIndex(doc, r.Page),
				PageObject: obj.PageObject,
			}); err != nil {
				return err
			}
		}
		for pg := range affected {
			if _, err := i.FPDFPage_Flatten(&requests.FPDFPage_Flatten{
				Page:  PageByIndex(doc, pg),
				Usage: requests.FPDFPage_FlattenUsageNormalDisplay,
			}); err != nil {
				return err
			}
		}
		return nil
	})
}

// Rect is a redaction region in PDF points (origin bottom-left).
type Rect struct {
	Page int
	X, Y, W, H float64
}

// NewDocument creates an empty document.
func (p *Pool) NewDocument() (references.FPDF_DOCUMENT, error) {
	var doc references.FPDF_DOCUMENT
	err := p.With(func(i pdfium.Pdfium) error {
		resp, err := i.FPDF_CreateNewDocument(&requests.FPDF_CreateNewDocument{})
		if err != nil {
			return err
		}
		doc = resp.Document
		return nil
	})
	return doc, err
}

// NewPage adds a blank page of the given size (points) at index.
func (p *Pool) NewPage(doc references.FPDF_DOCUMENT, index int, w, h float64) error {
	return p.With(func(i pdfium.Pdfium) error {
		_, err := i.FPDFPage_New(&requests.FPDFPage_New{
			Document:  doc,
			PageIndex: index,
			Width:     w,
			Height:    h,
		})
		return err
	})
}

// WatermarkImage stamps an RGBA image (with alpha) onto every page at the given
// position and scale. widthRatio/heightRatio are fractions of the page size;
// pos is one of "center", "tl", "tr", "bl", "br".
func (p *Pool) WatermarkImage(doc references.FPDF_DOCUMENT, img *image.RGBA, widthRatio, heightRatio float64, pos string) error {
	b := img.Bounds()
	w, h := b.Dx(), b.Dy()
	if w <= 0 || h <= 0 {
		return fmt.Errorf("empty watermark image")
	}
	return p.With(func(i pdfium.Pdfium) error {
		count, err := i.FPDF_GetPageCount(&requests.FPDF_GetPageCount{Document: doc})
		if err != nil {
			return err
		}
		for idx := range count.PageCount {
			page := PageByIndex(doc, idx)
			imgObj, err := i.FPDFPageObj_NewImageObj(&requests.FPDFPageObj_NewImageObj{Document: doc})
			if err != nil {
				return err
			}
			// Build a BGRA buffer from the RGBA image.
			buf := make([]byte, w*h*4)
			for y := range h {
				for x := range w {
					c := img.At(b.Min.X+x, b.Min.Y+y)
					r, g, bl, a := c.RGBA()
					// RGBA() returns 16-bit values (0..65535).
					o := (y*w + x) * 4
					buf[o+0] = byte(bl >> 8)
					buf[o+1] = byte(g >> 8)
					buf[o+2] = byte(r >> 8)
					buf[o+3] = byte(a >> 8)
				}
			}
			bmp, err := i.FPDFBitmap_CreateEx(&requests.FPDFBitmap_CreateEx{
				Width:  w,
				Height: h,
				Format: enums.FPDF_BITMAP_FORMAT_BGRA,
				Buffer: buf,
				Stride: w * 4,
			})
			if err != nil {
				return err
			}
			if _, err := i.FPDFImageObj_SetBitmap(&requests.FPDFImageObj_SetBitmap{
				Page:        &page,
				Count:       1,
				ImageObject: imgObj.PageObject,
				Bitmap:      bmp.Bitmap,
			}); err != nil {
				_, _ = i.FPDFBitmap_Destroy(&requests.FPDFBitmap_Destroy{Bitmap: bmp.Bitmap})
				return err
			}
			_, _ = i.FPDFBitmap_Destroy(&requests.FPDFBitmap_Destroy{Bitmap: bmp.Bitmap})

			pz, err := i.FPDF_GetPageSizeByIndexF(&requests.FPDF_GetPageSizeByIndexF{Document: doc, Index: idx})
			if err != nil {
				return err
			}
			pwW, pwH := float64(pz.Size.Width), float64(pz.Size.Height)
			wmW := pwW * widthRatio
			wmH := pwH * heightRatio
			e, f := 0.0, 0.0
			switch pos {
			case "tl":
				e, f = 24, pwH-24-wmH
			case "tr":
				e, f = pwW-24-wmW, pwH-24-wmH
			case "bl":
				e, f = 24, 24
			case "br":
				e, f = pwW-24-wmW, 24
			default: // center
				e, f = (pwW-wmW)/2, (pwH-wmH)/2
			}
			if _, err := i.FPDFImageObj_SetMatrix(&requests.FPDFImageObj_SetMatrix{
				ImageObject: imgObj.PageObject,
				Transform:   structs.FPDF_FS_MATRIX{A: float32(wmW), D: float32(wmH), E: float32(e), F: float32(f)},
			}); err != nil {
				return err
			}
			if _, err := i.FPDFPage_InsertObject(&requests.FPDFPage_InsertObject{
				Page:       page,
				PageObject: imgObj.PageObject,
			}); err != nil {
				return err
			}
		}
		return nil
	})
}
