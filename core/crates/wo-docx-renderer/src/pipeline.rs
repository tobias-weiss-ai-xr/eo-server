//! DOCX rendering pipeline.
//!
//! Provides the high-level API for converting DOCX documents
//! to rendered output. The pipeline stages are:
//!
//! 1. Parse DOCX (via wo-ooxml)
//! 2. Layout engine: compute text flow, page breaks, columns
//! 3. Render: draw to canvas using wo-renderer backend
//! 4. Export: output to PDF/PNG/SVG

use std::time::Instant;

use crate::layout::{LayoutElement, LayoutEngine, LayoutLine, LayoutPage};
use crate::model::*;
use pdf_writer::{Content, Name, Pdf, Rect, Ref, Str};
use wo_common::{CoreError, Result};
use wo_ooxml::model::{DocxBody, OoxmlFormat};
use wo_ooxml::parser::OoxmlParser;
use wo_renderer::{Canvas, Color, FontLibrary, Paint};

/// DOCX rendering pipeline.
pub struct DocxRenderPipeline {
    config: RenderConfig,
}

impl DocxRenderPipeline {
    pub fn new(config: RenderConfig) -> Self {
        Self { config }
    }

    /// Parse DOCX bytes into the document body (no layout or rendering).
    ///
    /// Validates the package is DOCX. Exposed so the conformance adapter can
    /// inspect font requests without re-parsing.
    pub fn parse_body(&self, docx_data: &[u8]) -> Result<DocxBody> {
        let parser = OoxmlParser::new();
        let ooxml = parser.parse(docx_data).map_err(|e| CoreError::Parse {
            format: "docx".into(),
            message: format!("Failed to parse DOCX: {}", e),
        })?;
        if ooxml.format != OoxmlFormat::Docx {
            return Err(CoreError::Parse {
                format: "docx".into(),
                message: format!("Expected DOCX format, got {}", ooxml.format),
            });
        }
        Ok(ooxml.docx_body.unwrap_or_else(DocxBody::default))
    }

    /// Parse and lay out a DOCX into pages, without rasterizing.
    ///
    /// This structured intermediate is what the wo-test-harness
    /// conformance-docx crate projects into its normalized IR.
    pub fn layout_pages(&self, docx_data: &[u8]) -> Result<Vec<LayoutPage>> {
        let body = self.parse_body(docx_data)?;
        let layout_engine = LayoutEngine::new(&self.config);
        Ok(layout_engine.layout(&body))
    }

    /// Render a DOCX document from raw bytes.
    pub fn render(&self, docx_data: &[u8]) -> Result<RenderResult> {
        let start = Instant::now();

        // Stage 1+2: parse + layout (shared with the conformance adapter)
        let pages = self.layout_pages(docx_data)?;

        // Stage 3: Render
        let page_count = pages.len() as u32;
        let warnings = Vec::new();

        // Stage 4: Export
        let output = match self.config.output_format {
            OutputFormat::Pdf => {
                let pdf_bytes =
                    generate_pdf(&pages, self.config.page_width, self.config.page_height);
                RenderOutput::Pdf(pdf_bytes)
            }
            OutputFormat::Png => {
                let scale = self.config.dpi as f32 / 72.0;
                let canvas_width = (self.config.page_width * scale) as u32;
                let canvas_height = (self.config.page_height * scale) as u32;
                let page_bytes: Vec<Vec<u8>> = pages
                    .iter()
                    .map(|page| {
                        let mut canvas = Canvas::new(canvas_width, canvas_height);
                        canvas.set_font_library(FontLibrary::new());
                        canvas.scale(scale, scale);
                        render_layout_page(&mut canvas, page);
                        canvas.to_rgba_bytes()
                    })
                    .collect();
                RenderOutput::Png(page_bytes)
            }
            OutputFormat::Svg => RenderOutput::Svg(Vec::new()),
        };

        let duration_ms = start.elapsed().as_millis() as u64;

        Ok(RenderResult {
            page_count,
            output,
            warnings,
            duration_ms,
        })
    }

    /// Render a single page by page number (1-based).
    pub fn render_page(&self, docx_data: &[u8], page_num: u32) -> Result<RenderOutput> {
        if page_num == 0 {
            return Err(CoreError::InvalidInput {
                message: "Page number must be 1-based".into(),
            });
        }

        // Delegate to full render, then extract the requested page
        let full_result = self.render(docx_data)?;
        let page_count = full_result.page_count;
        let page_idx = (page_num - 1) as usize;

        if page_num > page_count {
            return Err(CoreError::InvalidInput {
                message: format!(
                    "Page {} out of range ({} pages total)",
                    page_num, page_count
                ),
            });
        }

        match &full_result.output {
            RenderOutput::Pdf(_bytes) => {
                // Re-render with just the requested page for proper PDF output
                let config = self.config.clone();
                let layout_engine = LayoutEngine::new(&config);
                let body = // re-parse to get body
                    {
                        let parser = OoxmlParser::new();
                        let ooxml = parser.parse(docx_data).map_err(|e| CoreError::Parse {
                            format: "docx".into(),
                            message: format!("Failed to parse DOCX: {}", e),
                        })?;
                        ooxml.docx_body.unwrap_or_else(DocxBody::default)
                    };
                let all_pages = layout_engine.layout(&body);
                if page_idx < all_pages.len() {
                    let single_page = vec![all_pages[page_idx].clone()];
                    let pdf_bytes =
                        generate_pdf(&single_page, config.page_width, config.page_height);
                    Ok(RenderOutput::Pdf(pdf_bytes))
                } else {
                    Err(CoreError::InvalidInput {
                        message: format!(
                            "Page {} out of range ({} pages total)",
                            page_num,
                            all_pages.len()
                        ),
                    })
                }
            }
            RenderOutput::Png(pages) => {
                if page_idx < pages.len() {
                    Ok(RenderOutput::Png(vec![pages[page_idx].clone()]))
                } else {
                    Err(CoreError::InvalidInput {
                        message: format!(
                            "Page {} out of range ({} pages total)",
                            page_num,
                            pages.len()
                        ),
                    })
                }
            }
            RenderOutput::Svg(pages) => {
                if page_idx < pages.len() {
                    Ok(RenderOutput::Svg(vec![pages[page_idx].clone()]))
                } else {
                    Err(CoreError::InvalidInput {
                        message: format!(
                            "Page {} out of range ({} pages total)",
                            page_num,
                            pages.len()
                        ),
                    })
                }
            }
        }
    }

    /// Get the rendering configuration.
    pub fn config(&self) -> &RenderConfig {
        &self.config
    }

    /// Update the rendering configuration.
    pub fn set_config(&mut self, config: RenderConfig) {
        self.config = config;
    }
}

impl Default for DocxRenderPipeline {
    fn default() -> Self {
        Self::new(RenderConfig::default())
    }
}

/// Generate a valid PDF document from layout pages.
///
/// Creates a PDF with:
/// - Catalog → Pages → individual Page objects
/// - Type1 Helvetica font resource
/// - Content streams with text operators for each page's text
/// - Proper cross-reference table and trailer
fn generate_pdf(pages: &[LayoutPage], page_width: f32, page_height: f32) -> Vec<u8> {
    let mut pdf = Pdf::new();

    // Reserve object IDs:
    // 1 = catalog, 2 = page tree, 3 = font,
    // 4..(4+n) = pages, (4+n)..(4+2n) = content streams
    let catalog_id = Ref::new(1);
    let page_tree_id = Ref::new(2);
    let font_id = Ref::new(3);
    let font_name = Name(b"F1");

    let num_pages = pages.len().max(1);
    let page_ids: Vec<Ref> = (0..num_pages).map(|i| Ref::new(4 + i as i32)).collect();
    let content_start = 4 + num_pages as i32;
    let content_ids: Vec<Ref> = (0..num_pages)
        .map(|i| Ref::new(content_start + i as i32))
        .collect();

    // Catalog
    pdf.catalog(catalog_id).pages(page_tree_id);

    // Page tree (finish immediately to release borrow)
    {
        let mut pages_tree = pdf.pages(page_tree_id);
        pages_tree.kids(page_ids.iter().copied());
        pages_tree.count(num_pages as i32);
    }

    // Font (Type1 Helvetica - standard PDF base font, no embedding needed)
    pdf.type1_font(font_id).base_font(Name(b"Helvetica"));

    // Write each page and its content stream
    for (i, page) in pages.iter().enumerate() {
        let page_id = page_ids[i];
        let content_id = content_ids[i];

        // Build content stream for this page
        let mut content = Content::new();
        let text_lines = extract_text_lines(page);

        for line in &text_lines {
            content.begin_text();
            content.set_font(font_name, line.font_size);
            // PDF coordinates: origin at bottom-left, y increases upward.
            // Layout y is from top, so flip: pdf_y = page_height - layout_y
            let pdf_y = page_height - line.y;
            content.next_line(line.x, pdf_y);
            // Show text (escape parentheses and backslashes for PDF strings)
            let escaped = escape_pdf_string(&line.text);
            content.show(Str(escaped.as_bytes()));
            content.end_text();
        }

        // Write page object (finish immediately to release borrow)
        {
            let mut page_obj = pdf.page(page_id);
            page_obj.media_box(Rect::new(0.0, 0.0, page_width, page_height));
            page_obj.parent(page_tree_id);
            page_obj.contents(content_id);
            page_obj.resources().fonts().pair(font_name, font_id);
        }

        // Write content stream
        pdf.stream(content_id, &content.finish());
    }

    pdf.finish()
}

/// A text line extracted from layout for PDF generation.
struct PdfTextLine {
    text: String,
    x: f32,
    y: f32,
    font_size: f32,
}

/// Extract all text lines from a layout page.
fn extract_text_lines(page: &LayoutPage) -> Vec<PdfTextLine> {
    let mut lines = Vec::new();

    for element in &page.elements {
        match element {
            LayoutElement::Paragraph {
                lines: para_lines, ..
            } => {
                for line in para_lines {
                    if !line.text.is_empty() {
                        lines.push(PdfTextLine {
                            text: line.text.clone(),
                            x: line.x,
                            y: line.y,
                            font_size: line.font_size,
                        });
                    }
                }
            }
            LayoutElement::Table { cells, .. } => {
                for cell in cells {
                    let mut text_y = cell.y + 4.0; // small padding
                    for para in &cell.paragraphs {
                        for run in &para.runs {
                            if run.text.is_empty() {
                                continue;
                            }
                            let font_size = run.font_size.unwrap_or(24) as f32 / 2.0;
                            lines.push(PdfTextLine {
                                text: run.text.clone(),
                                x: cell.x + 4.0,
                                y: text_y,
                                font_size,
                            });
                            text_y += font_size * 1.2;
                        }
                    }
                }
            }
            LayoutElement::PageBreak => {}
        }
    }

    lines
}

/// Escape special PDF string characters: parentheses and backslash.
fn escape_pdf_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '(' => out.push_str("\\("),
            ')' => out.push_str("\\)"),
            _ => out.push(ch),
        }
    }
    out
}

/// Render a single layout page to a canvas.
fn render_layout_page(canvas: &mut Canvas, page: &crate::layout::LayoutPage) {
    for element in &page.elements {
        match element {
            LayoutElement::Paragraph {
                lines,
                alignment: _,
            } => {
                for line in lines {
                    render_line(canvas, line);
                }
            }
            LayoutElement::Table {
                cells,
                row_heights: _,
                x: _,
                y: _,
                width: _,
            } => {
                for cell in cells {
                    render_table_cell(canvas, cell);
                }
            }
            LayoutElement::PageBreak => {
                // Nothing to render for page breaks in pixel output;
                // page breaks are handled by layout engine producing new pages.
            }
        }
    }
}

/// Render a single text line to the canvas.
fn render_line(canvas: &mut Canvas, line: &LayoutLine) {
    if line.text.is_empty() {
        return;
    }

    // Parse text color from hex string
    let r = parse_hex_color_part(&line.color, 0) as f32 / 255.0;
    let g = parse_hex_color_part(&line.color, 1) as f32 / 255.0;
    let b = parse_hex_color_part(&line.color, 2) as f32 / 255.0;
    let color = Color::rgb(r, g, b);

    // Baseline y = top + ascent (≈80% of font size)
    let baseline_y = line.y + line.font_size * 0.8;

    canvas.draw_text(
        &line.text,
        line.x as f64,
        baseline_y as f64,
        line.font_size as f64,
        "sans-serif",
        color,
    );
}

/// Render a table cell: draw borders and text content.
fn render_table_cell(canvas: &mut Canvas, cell: &crate::layout::LayoutCell) {
    // Draw cell border
    canvas.save();
    canvas.set_stroke(Paint::Color(Color::rgb(0.3, 0.3, 0.3)));
    canvas.set_stroke_style(wo_renderer::StrokeStyle::default());
    canvas.stroke_rect(cell.x, cell.y, cell.width, cell.height);
    canvas.restore();

    // Render cell paragraphs as simple text blocks
    let mut text_y = cell.y + 4.0; // small padding
    for para in &cell.paragraphs {
        for run in &para.runs {
            if run.text.is_empty() {
                continue;
            }

            let font_size = run.font_size.unwrap_or(24) as f32 / 2.0;
            let r = run
                .color
                .as_ref()
                .map(|c| parse_hex_color_part(c, 0) as f32 / 255.0)
                .unwrap_or(0.0);
            let g = run
                .color
                .as_ref()
                .map(|c| parse_hex_color_part(c, 1) as f32 / 255.0)
                .unwrap_or(0.0);
            let b = run
                .color
                .as_ref()
                .map(|c| parse_hex_color_part(c, 2) as f32 / 255.0)
                .unwrap_or(0.0);

            let color = Color::rgb(r, g, b);
            let baseline_y = text_y + font_size * 0.8;

            canvas.draw_text(
                &run.text,
                (cell.x + 4.0) as f64,
                baseline_y as f64,
                font_size as f64,
                "sans-serif",
                color,
            );
            text_y += font_size * 1.2;
        }
    }
}

/// Parse a hex color like "FF0000" and return the byte value for component 0=R, 1=G, 2=B.
fn parse_hex_color_part(hex: &str, component: usize) -> u8 {
    let start = component * 2;
    if hex.len() >= start + 2 {
        u8::from_str_radix(&hex[start..start + 2], 16).unwrap_or(0)
    } else {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn make_minimal_docx() -> Vec<u8> {
        let mut buf = Vec::new();
        {
            let mut zip = zip::ZipWriter::new(std::io::Cursor::new(&mut buf));
            zip.start_file(
                "[Content_Types].xml",
                zip::write::SimpleFileOptions::default(),
            )
            .unwrap();
            zip.write_all(br#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"#)
                .unwrap();

            zip.start_file("_rels/.rels", zip::write::SimpleFileOptions::default())
                .unwrap();
            zip.write_all(br#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"#)
                .unwrap();

            zip.start_file(
                "word/document.xml",
                zip::write::SimpleFileOptions::default(),
            )
            .unwrap();
            zip.write_all(
                br#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Hello World</w:t></w:r></w:p></w:body>
</w:document>"#,
            )
            .unwrap();

            zip.finish().unwrap();
        }
        buf
    }

    fn make_docx_with_body(document_xml: &str) -> Vec<u8> {
        let mut buf = Vec::new();
        {
            let mut zip = zip::ZipWriter::new(std::io::Cursor::new(&mut buf));
            zip.start_file(
                "[Content_Types].xml",
                zip::write::SimpleFileOptions::default(),
            )
            .unwrap();
            zip.write_all(br#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"#)
                .unwrap();

            zip.start_file("_rels/.rels", zip::write::SimpleFileOptions::default())
                .unwrap();
            zip.write_all(br#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"#)
                .unwrap();

            zip.start_file(
                "word/document.xml",
                zip::write::SimpleFileOptions::default(),
            )
            .unwrap();
            zip.write_all(document_xml.as_bytes()).unwrap();

            zip.finish().unwrap();
        }
        buf
    }

    #[test]
    fn test_default_config() {
        let config = RenderConfig::default();
        assert_eq!(config.output_format, OutputFormat::Pdf);
        assert!((config.page_width - 595.28).abs() < 0.01);
        assert!((config.page_height - 841.89).abs() < 0.01);
        assert_eq!(config.dpi, 150);
        assert!(!config.render_comments);
        assert!(!config.render_changes);
    }

    #[test]
    fn test_default_margins() {
        let margins = Margins::default();
        assert!((margins.top - 72.0).abs() < 0.01);
        assert!((margins.left - 72.0).abs() < 0.01);
    }

    #[test]
    fn test_pipeline_rejects_non_zip() {
        let pipeline = DocxRenderPipeline::default();
        let result = pipeline.render(b"not a zip");
        assert!(result.is_err());
    }

    #[test]
    fn test_pipeline_accepts_zip() {
        let pipeline = DocxRenderPipeline::default();
        let data = make_minimal_docx();
        let result = pipeline.render(&data).unwrap();
        assert!(result.page_count >= 1);
    }

    #[test]
    fn test_render_single_page_pdf() {
        let pipeline = DocxRenderPipeline::default();
        let data = make_minimal_docx();
        // PDF output now supports single-page extraction
        let result = pipeline.render_page(&data, 1);
        assert!(result.is_ok());
        if let RenderOutput::Pdf(bytes) = result.unwrap() {
            assert!(
                bytes.starts_with(b"%PDF-"),
                "PDF should start with %PDF- header"
            );
        } else {
            panic!("Expected PDF output");
        }
    }

    #[test]
    fn test_config_getter() {
        let config = RenderConfig::default();
        let pipeline = DocxRenderPipeline::new(config);
        assert_eq!(pipeline.config().dpi, 150);
    }

    #[test]
    fn test_set_config() {
        let mut pipeline = DocxRenderPipeline::default();
        let config = RenderConfig {
            dpi: 300,
            ..Default::default()
        };
        pipeline.set_config(config);
        assert_eq!(pipeline.config().dpi, 300);
    }

    #[test]
    fn test_render_minimal_docx() {
        let pipeline = DocxRenderPipeline::default();
        let data = make_minimal_docx();
        let result = pipeline.render(&data).unwrap();
        assert!(result.page_count >= 1, "Should have at least 1 page");
        match &result.output {
            RenderOutput::Pdf(bytes) => {
                assert!(!bytes.is_empty(), "Output should not be empty");
            }
            RenderOutput::Png(pages) => {
                assert!(!pages.is_empty());
                assert!(!pages[0].is_empty());
            }
            RenderOutput::Svg(_) => {}
        }
    }

    #[test]
    fn test_render_text_wrapping() {
        let pipeline = DocxRenderPipeline::default();
        let docx = make_docx_with_body(
            r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>AAAAAAAAAABBBBBBBBBBCCCCCCCCCCDDDDDDDDDD</w:t></w:r></w:p>
  </w:body>
</w:document>"#,
        );
        let result = pipeline.render(&docx).unwrap();
        assert!(result.page_count >= 1);
    }

    #[test]
    fn test_render_page_breaks() {
        let pipeline = DocxRenderPipeline::default();
        let docx = make_docx_with_body(
            r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Page 1</w:t></w:r></w:p>
    <w:p>
      <w:pPr><w:pageBreakBefore/></w:pPr>
      <w:r><w:t>Page 2</w:t></w:r>
    </w:p>
  </w:body>
</w:document>"#,
        );
        let result = pipeline.render(&docx).unwrap();
        assert!(
            result.page_count >= 2,
            "page_break_before should create new page"
        );
    }

    #[test]
    fn test_render_table() {
        let pipeline = DocxRenderPipeline::default();
        let docx = make_docx_with_body(
            r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tblPr><w:tblW w="5000"/></w:tblPr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>"#,
        );
        let result = pipeline.render(&docx).unwrap();
        assert!(result.page_count >= 1);
        match &result.output {
            RenderOutput::Pdf(bytes) => assert!(!bytes.is_empty()),
            RenderOutput::Png(pages) => {
                assert!(!pages.is_empty());
                assert!(!pages[0].is_empty());
            }
            RenderOutput::Svg(_) => {}
        }
    }

    #[test]
    fn test_render_page_out_of_range() {
        let pipeline = DocxRenderPipeline::default();
        let data = make_minimal_docx();
        let result = pipeline.render_page(&data, 99);
        assert!(result.is_err());
    }

    #[test]
    fn test_render_page_zero() {
        let pipeline = DocxRenderPipeline::default();
        let data = make_minimal_docx();
        let result = pipeline.render_page(&data, 0);
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_hex_color() {
        assert_eq!(parse_hex_color_part("FF0000", 0), 0xFF);
        assert_eq!(parse_hex_color_part("FF0000", 1), 0x00);
        assert_eq!(parse_hex_color_part("FF0000", 2), 0x00);
        assert_eq!(parse_hex_color_part("00FF00", 0), 0x00);
        assert_eq!(parse_hex_color_part("00FF00", 1), 0xFF);
        assert_eq!(parse_hex_color_part("", 0), 0);
    }

    #[test]
    fn test_render_multiple_paragraphs() {
        let pipeline = DocxRenderPipeline::default();
        let docx = make_docx_with_body(
            r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>First</w:t></w:r></w:p>
    <w:p><w:r><w:t>Second</w:t></w:r></w:p>
    <w:p><w:r><w:t>Third</w:t></w:r></w:p>
  </w:body>
</w:document>"#,
        );
        let result = pipeline.render(&docx).unwrap();
        assert!(result.page_count >= 1);
    }

    #[test]
    fn test_pdf_output_starts_with_header() {
        let pipeline = DocxRenderPipeline::default();
        let data = make_minimal_docx();
        let result = pipeline.render(&data).unwrap();
        match &result.output {
            RenderOutput::Pdf(bytes) => {
                assert!(
                    bytes.starts_with(b"%PDF-"),
                    "PDF output must start with %PDF- header, got: {:?}",
                    &bytes[..bytes.len().min(20)]
                );
            }
            _ => panic!("Expected PDF output with default config"),
        }
    }

    #[test]
    fn test_pdf_structure_is_valid() {
        let pipeline = DocxRenderPipeline::default();
        let data = make_minimal_docx();
        let result = pipeline.render(&data).unwrap();
        match &result.output {
            RenderOutput::Pdf(bytes) => {
                let s = String::from_utf8_lossy(bytes);
                // Must have PDF header
                assert!(s.starts_with("%PDF-"), "Missing PDF header");
                // Must have EOF marker
                assert!(s.contains("%%EOF"), "Missing %%EOF trailer");
                // Must have catalog reference
                assert!(s.contains("/Type /Catalog"), "Missing /Type /Catalog");
                // Must have pages reference
                assert!(s.contains("/Type /Pages"), "Missing /Type /Pages");
                // Must have page objects
                assert!(s.contains("/Type /Page"), "Missing /Type /Page");
                // Must have Helvetica font
                assert!(s.contains("/Helvetica"), "Missing Helvetica font");
                // Must have font resource
                assert!(s.contains("/Font"), "Missing /Font resource");
                // Must have content stream with text operators
                assert!(s.contains("BT"), "Missing BT (begin text) operator");
                assert!(s.contains("ET"), "Missing ET (end text) operator");
            }
            _ => panic!("Expected PDF output"),
        }
    }

    #[test]
    fn test_pdf_multi_page_structure() {
        let pipeline = DocxRenderPipeline::default();
        let docx = make_docx_with_body(
            r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Page 1</w:t></w:r></w:p>
    <w:p>
      <w:pPr><w:pageBreakBefore/></w:pPr>
      <w:r><w:t>Page 2</w:t></w:r>
    </w:p>
  </w:body>
</w:document>"#,
        );
        let result = pipeline.render(&docx).unwrap();
        assert!(result.page_count >= 2, "Should have at least 2 pages");
        match &result.output {
            RenderOutput::Pdf(bytes) => {
                assert!(
                    bytes.starts_with(b"%PDF-"),
                    "Multi-page PDF must have header"
                );
                let s = String::from_utf8_lossy(bytes);
                // Multiple /Type /Page entries (one per page)
                let page_count = s.matches("/Type /Page").count();
                assert!(
                    page_count >= 2,
                    "Expected at least 2 /Type /Page entries, found {}",
                    page_count
                );
            }
            _ => panic!("Expected PDF output"),
        }
    }
}
