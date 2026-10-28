package server

import (
	"embed"
	"image"
	"image/color"

	"golang.org/x/image/font"
	"golang.org/x/image/font/opentype"
	"golang.org/x/image/math/fixed"
)

//go:embed wmfont/DejaVuSans.ttf
var fontFS embed.FS

var (
	wmFont     *opentype.Font
	wmFontOnce bool
)

func watermarkFont() *opentype.Font {
	if wmFontOnce {
		return wmFont
	}
	data, _ := fontFS.ReadFile("wmfont/DejaVuSans.ttf")
	if f, err := opentype.Parse(data); err == nil {
		wmFont = f
	}
	wmFontOnce = true
	return wmFont
}

// renderTextImage renders text into a transparent RGBA image sized to fit.
// fontSize is in pixels.
func renderTextImage(text string, fontSize int) (*image.RGBA, error) {
	f := watermarkFont()
	if f == nil {
		return nil, nil
	}
	if fontSize < 8 {
		fontSize = 48
	}
	face, err := opentype.NewFace(f, &opentype.FaceOptions{
		Size:    float64(fontSize),
		DPI:     72,
		Hinting: font.HintingFull,
	})
	if err != nil {
		return nil, err
	}
	defer face.Close()

	metrics := face.Metrics()
	// Measure via a throwaway drawer.
	measure := &font.Drawer{Dst: image.NewRGBA(image.Rect(0, 0, 1, 1)), Face: face}
	advance := measure.MeasureString(text)
	width := int(advance.Ceil()) + 8
	height := int(metrics.Height.Ceil()) + 8
	if width < 8 {
		width = 8
	}
	if height < 8 {
		height = 8
	}

	img := image.NewRGBA(image.Rect(0, 0, width, height))
	d := &font.Drawer{
		Dst:  img,
		Src:  image.NewUniform(color.Black),
		Face: face,
		// Baseline so the text sits vertically centered.
		Dot: fixed.P(4, int(metrics.Ascent.Ceil())+4),
	}
	d.DrawString(text)
	return img, nil
}
