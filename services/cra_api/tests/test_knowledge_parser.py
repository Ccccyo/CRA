import io
import zipfile

from cra.ingest import knowledge_parser
from cra.ingest.knowledge_parser import parse_knowledge_source
from cra.ingest.pdf_reader import Page


def test_parses_utf8_text_as_one_source_document():
    parsed = parse_knowledge_source(
        "# 政策\n\n所有重大偏离需要人工复核。".encode(),
        "policy.md",
    )

    assert parsed.parser == "text"
    assert parsed.mime_type == "text/markdown"
    assert "人工复核" in parsed.content
    assert parsed.pages is None


def test_parses_docx_paragraphs_and_tables():
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>信用政策</w:t></w:r></w:p>
        <w:p><w:r><w:t>建议额度需要留痕。</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>等级</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
      </w:body>
    </w:document>"""
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    parsed = parse_knowledge_source(payload.getvalue(), "policy.docx")

    assert parsed.parser == "docx"
    assert "# 信用政策" in parsed.content
    assert "等级 | A" in parsed.content


def test_image_uses_ocr_and_keeps_page_semantics(monkeypatch):
    monkeypatch.setattr(
        knowledge_parser,
        "load_document_text",
        lambda path, use_ocr=None: [Page(1, "OCR 识别后的制度内容")],
    )

    parsed = parse_knowledge_source(b"fake-image", "scan.png")

    assert parsed.parser == "image-ocr"
    assert parsed.pages and parsed.pages[0].number == 1
    assert "OCR 识别" in parsed.content


def test_pdf_table_continuation_repeats_previous_header():
    pages = [Page(1, "资产负债表"), Page(2, "续表")]
    tables = {
        1: [[
            ["科目", "2025", "2024"],
            ["流动资产", "100", "90"],
        ]],
        2: [[
            ["资产总计", "200", "180"],
            ["负债合计", "120", "110"],
        ]],
    }

    result = knowledge_parser._pages_with_tables(pages, tables)

    assert "| 科目 | 2025 | 2024 |" in result[1].text
    assert "| 资产总计 | 200 | 180 |" in result[1].text
