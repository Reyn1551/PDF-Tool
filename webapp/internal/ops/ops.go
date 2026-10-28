// Package ops implements the user-facing PDF operations as pure functions
// over the pdfiumx pool (plus Ghostscript for the two things PDFium cannot do:
// recompression and encryption).
//
// Every mutating op returns the resulting bytes and page count; the server
// re-opens those bytes into a fresh document handle and bumps the session
// version. Keeping ops pure (in bytes, out bytes) makes them trivially testable
// and keeps the session store free of PDFium state.
package ops

import (
	"bytes"
	"fmt"
	"image"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"pdftoolweb/internal/pdfiumx"
)

// Pool is the pdfiumx pool.
type Pool = pdfiumx.Pool

// Doc is an open PDFium document handle.
type Doc = pdfiumx.Doc

// OpenBytes opens bytes as a document, returning the handle and page count.
func OpenBytes(p *Pool, data []byte, password string) (Doc, int, error) {
	d, err := p.Open(data, password)
	if err != nil {
		return "", 0, err
	}
	n, err := p.PageCount(d)
	if err != nil {
		p.CloseDocument(d)
		return "", 0, err
	}
	return d, n, nil
}

// ---- organize -----------------------------------------------------------

// appendPages imports the given source pages onto the end of dst.
func appendPages(p *Pool, dst, src Doc, indices []int) error {
	n, err := p.PageCount(dst)
	if err != nil {
		return err
	}
	return p.ImportPages(dst, src, indices, n)
}

// Merge concatenates the given documents in order.
func Merge(p *Pool, docs []Doc) ([]byte, int, error) {
	if len(docs) == 0 {
		return nil, 0, fmt.Errorf("nothing to merge")
	}
	out, err := p.NewDocument()
	if err != nil {
		return nil, 0, err
	}
	defer p.CloseDocument(out)
	for _, d := range docs {
		n, err := p.PageCount(d)
		if err != nil {
			return nil, 0, err
		}
		indices := make([]int, n)
		for i := range n {
			indices[i] = i
		}
		if err := appendPages(p, out, d, indices); err != nil {
			return nil, 0, err
		}
	}
	total, _ := p.PageCount(out)
	data, err := p.Save(out)
	return data, total, err
}

// DeletePages removes the given 0-based page indices.
func DeletePages(p *Pool, doc Doc, indices []int) ([]byte, int, error) {
	total, err := p.PageCount(doc)
	if err != nil {
		return nil, 0, err
	}
	keep := map[int]bool{}
	for _, i := range indices {
		if i >= 0 && i < total {
			keep[i] = true
		}
	}
	out, err := p.NewDocument()
	if err != nil {
		return nil, 0, err
	}
	defer p.CloseDocument(out)
	kept := 0
	for i := range total {
		if !keep[i] {
			if err := appendPages(p, out, doc, []int{i}); err != nil {
				return nil, 0, err
			}
			kept++
		}
	}
	data, err := p.Save(out)
	return data, kept, err
}

// Rotate rotates the given pages by deg (90/180/270).
func Rotate(p *Pool, doc Doc, indices []int, deg int) ([]byte, int, error) {
	for _, i := range indices {
		if err := p.RotatePage(doc, i, deg); err != nil {
			return nil, 0, err
		}
	}
	n, _ := p.PageCount(doc)
	data, err := p.Save(doc)
	return data, n, err
}

// Reorder applies a permutation: order[i] is the source index that becomes
// page i.
func Reorder(p *Pool, doc Doc, order []int) ([]byte, int, error) {
	total, err := p.PageCount(doc)
	if err != nil {
		return nil, 0, err
	}
	if len(order) != total {
		return nil, 0, fmt.Errorf("order length %d != page count %d", len(order), total)
	}
	out, err := p.NewDocument()
	if err != nil {
		return nil, 0, err
	}
	defer p.CloseDocument(out)
	for _, src := range order {
		if src < 0 || src >= total {
			return nil, 0, fmt.Errorf("reorder index %d out of range", src)
		}
		if err := appendPages(p, out, doc, []int{src}); err != nil {
			return nil, 0, err
		}
	}
	data, err := p.Save(out)
	return data, total, err
}

// Reverse reverses page order.
func Reverse(p *Pool, doc Doc) ([]byte, int, error) {
	total, err := p.PageCount(doc)
	if err != nil {
		return nil, 0, err
	}
	order := make([]int, total)
	for i := range total {
		order[i] = total - 1 - i
	}
	return Reorder(p, doc, order)
}

// SplitRange extracts pages [start,end] (1-based, inclusive) into a new doc.
func SplitRange(p *Pool, doc Doc, start, end int) ([]byte, int, error) {
	total, err := p.PageCount(doc)
	if err != nil {
		return nil, 0, err
	}
	if start < 1 {
		start = 1
	}
	if end > total {
		end = total
	}
	if start > end {
		return nil, 0, fmt.Errorf("invalid range %d-%d", start, end)
	}
	out, err := p.NewDocument()
	if err != nil {
		return nil, 0, err
	}
	defer p.CloseDocument(out)
	indices := make([]int, end-start+1)
	for i := range len(indices) {
		indices[i] = start - 1 + i
	}
	if err := appendPages(p, out, doc, indices); err != nil {
		return nil, 0, err
	}
	data, err := p.Save(out)
	return data, len(indices), err
}

// ---- security -----------------------------------------------------------

// Watermark stamps an RGBA image onto every page.
func Watermark(p *Pool, doc Doc, img *image.RGBA, wRatio, hRatio float64, pos string) ([]byte, int, error) {
	if err := p.WatermarkImage(doc, img, wRatio, hRatio, pos); err != nil {
		return nil, 0, err
	}
	n, _ := p.PageCount(doc)
	data, err := p.Save(doc)
	return data, n, err
}

// Redact white-outs the given rectangles (points, origin bottom-left).
func Redact(p *Pool, doc Doc, rects []pdfiumx.Rect) ([]byte, int, error) {
	if err := p.Redact(doc, rects); err != nil {
		return nil, 0, err
	}
	n, _ := p.PageCount(doc)
	data, err := p.Save(doc)
	return data, n, err
}

// Protect encrypts with a user (open) and owner (permissions) password via
// Ghostscript.
func Protect(data []byte, user, owner string) ([]byte, error) {
	args := []string{
		"-dNOPAUSE", "-dBATCH", "-dQUIET", "-sDEVICE=pdfwrite",
		"-dEncryptIdentityH",
		"-sUserPassword=" + user,
	}
	if owner != "" {
		args = append(args, "-sOwnerPassword="+owner)
	}
	return gsRun(data, args)
}

// Unlock removes encryption using the user password.
func Unlock(data []byte, password string) ([]byte, error) {
	return gsRun(data, []string{
		"-dNOPAUSE", "-dBATCH", "-dQUIET", "-sDEVICE=pdfwrite",
		"-sPDFPassword=" + password,
	})
}

// Compress re-encodes with Ghostscript. level: 0=screen(72) 1=ebook(150)
// 2=printer(300).
func Compress(data []byte, level int) ([]byte, error) {
	quality := map[int]string{0: "/screen", 1: "/ebook", 2: "/printer"}
	q, ok := quality[level]
	if !ok {
		q = "/ebook"
	}
	return gsRun(data, []string{
		"-dNOPAUSE", "-dBATCH", "-dQUIET", "-sDEVICE=pdfwrite",
		"-dPDFSETTINGS=" + q,
	})
}

// gsRun runs Ghostscript over data (written to a temp file, because gs cannot
// seek stdin) and returns the PDF it writes.
func gsRun(data []byte, args []string) ([]byte, error) {
	gs, err := exec.LookPath("gs")
	if err != nil {
		return nil, fmt.Errorf("ghostscript not installed: %w", err)
	}
	dir, err := os.MkdirTemp("", "pdftool-gs-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	inPath := filepath.Join(dir, "in.pdf")
	outPath := filepath.Join(dir, "out.pdf")
	if err := os.WriteFile(inPath, data, 0o600); err != nil {
		return nil, err
	}
	full := append([]string{"-o", outPath}, args...)
	full = append(full, inPath)
	cmd := exec.Command(gs, full...)
	var gerr bytes.Buffer
	cmd.Stderr = &gerr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("gs: %v: %s", err, gerr.String())
	}
	out, err := os.ReadFile(outPath)
	if err != nil {
		return nil, fmt.Errorf("gs output: %w", err)
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("gs produced no output: %s", gerr.String())
	}
	return out, nil
}

// ---- export -------------------------------------------------------------

// Text extracts plain text for the given 0-based pages (nil = all).
func Text(p *Pool, doc Doc, pages []int) (string, error) {
	total, err := p.PageCount(doc)
	if err != nil {
		return "", err
	}
	if pages == nil {
		pages = make([]int, total)
		for i := range total {
			pages[i] = i
		}
	}
	var b strings.Builder
	for _, pg := range pages {
		if pg < 0 || pg >= total {
			continue
		}
		t, err := p.PageText(doc, pg)
		if err != nil {
			return "", err
		}
		if b.Len() > 0 {
			b.WriteString("\n\n")
		}
		fmt.Fprintf(&b, "----- Page %d -----\n%s", pg+1, t)
	}
	return b.String(), nil
}

// ExportImages renders the given pages to PNG or JPEG.
func ExportImages(p *Pool, doc Doc, pages []int, width, quality int, jpeg bool) (map[string][]byte, error) {
	total, err := p.PageCount(doc)
	if err != nil {
		return nil, err
	}
	if pages == nil {
		pages = make([]int, total)
		for i := range total {
			pages[i] = i
		}
	}
	res := map[string][]byte{}
	for _, pg := range pages {
		if pg < 0 || pg >= total {
			continue
		}
		name := fmt.Sprintf("page-%03d", pg+1)
		if jpeg {
			b, err := p.RenderJPEG(doc, pg, width, quality)
			if err != nil {
				return nil, err
			}
			res[name+".jpg"] = b
		} else {
			b, _, _, err := p.RenderPNG(doc, pg, width, 0)
			if err != nil {
				return nil, err
			}
			res[name+".png"] = b
		}
	}
	return res, nil
}
