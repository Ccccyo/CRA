"""Parse knowledge-library source files into one authoritative full-text document.

The returned text is what administrators review and what the internal chunker
indexes.  Chunk boundaries are deliberately not part of the reader-facing API.
"""
from __future__ import annotations

import mimetypes
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree

from cra.ingest.pdf_reader import Page, load_document_text


TEXT_EXTENSIONS = {".md", ".txt"}
PDF_EXTENSIONS = {".pdf"}
WORD_EXTENSIONS = {".doc", ".docx"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
SUPPORTED_EXTENSIONS = (
    TEXT_EXTENSIONS | PDF_EXTENSIONS | WORD_EXTENSIONS | IMAGE_EXTENSIONS
)


@dataclass(frozen=True)
class ParsedKnowledgeSource:
    content: str
    mime_type: str
    parser: str
    pages: Optional[List[Page]] = None
    ocr_backend: str = ""
    ocr_page_numbers: tuple[int, ...] = ()


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文本文件编码无法识别，请使用 UTF-8 或 GB18030")


def _docx_text(path: Path) -> str:
    """Extract paragraphs and tables from DOCX with the standard library."""
    try:
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError("Word 文件损坏或不是有效的 .docx 文档") from exc

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def node_text(node) -> str:
        return "".join(part.text or "" for part in node.iter(f"{namespace}t")).strip()

    blocks: List[str] = []
    body = root.find(f"{namespace}body")
    if body is None:
        return ""
    for child in body:
        if child.tag == f"{namespace}p":
            text = node_text(child)
            if not text:
                continue
            style = child.find(f".//{namespace}pStyle")
            style_name = style.get(f"{namespace}val", "") if style is not None else ""
            if style_name.lower().startswith("heading"):
                digits = "".join(character for character in style_name if character.isdigit())
                level = min(max(int(digits or "1"), 1), 6)
                text = f"{'#' * level} {text}"
            blocks.append(text)
        elif child.tag == f"{namespace}tbl":
            rows = []
            for row in child.findall(f"{namespace}tr"):
                cells = [node_text(cell) for cell in row.findall(f"{namespace}tc")]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append("\n".join(rows))
    return "\n\n".join(blocks)


def _legacy_doc_text(path: Path) -> str:
    converter = shutil.which("textutil")
    command = [converter, "-convert", "txt", "-stdout", str(path)] if converter else []
    if not command:
        converter = shutil.which("antiword")
        command = [converter, str(path)] if converter else []
    if not command:
        raise ValueError("当前服务器缺少 .doc 解析器；请上传 .docx，或安装 textutil/antiword")
    result = subprocess.run(command, capture_output=True, check=False, timeout=60)
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError("无法解析该 .doc 文件，请另存为 .docx 后重试")
    return _decode_text(result.stdout)


def _table_row(row: List[str]) -> str:
    cells = [str(cell or "").replace("\n", " ").replace("|", "\\|").strip() for cell in row]
    return "| " + " | ".join(cells) + " |"


def _looks_like_continuation(row: List[str]) -> bool:
    if len(row) < 2:
        return False
    numeric = sum(
        bool(re.search(r"\(?-?\d[\d,.]*\)?%?", str(cell or "")))
        for cell in row[1:]
    )
    years_only = all(
        not str(cell or "").strip()
        or bool(re.fullmatch(r"(?:19|20)\d{2}", str(cell or "").strip()))
        for cell in row[1:]
    )
    return numeric >= max(1, len(row[1:]) // 2) and not years_only


def _pages_with_tables(
    pages: List[Page], tables: dict[int, List[List[List[str]]]]
) -> List[Page]:
    """Append Markdown tables and carry a prior header onto continuation pages."""
    previous_header: Optional[List[str]] = None
    result: List[Page] = []
    for page in pages:
        rendered = []
        for table in tables.get(page.number, []):
            rows = [row for row in table if any(str(cell or "").strip() for cell in row)]
            if not rows:
                continue
            width = len(rows[0])
            if (
                previous_header
                and len(previous_header) == width
                and _looks_like_continuation(rows[0])
            ):
                rows = [previous_header, *rows]
            else:
                previous_header = rows[0]
            header = rows[0]
            rendered.append(
                "\n".join(
                    [
                        _table_row(header),
                        _table_row(["---"] * len(header)),
                        *[_table_row(row) for row in rows[1:]],
                    ]
                )
            )
        table_text = "\n\n".join(rendered)
        text = page.text
        if table_text:
            text = f"{text.rstrip()}\n\n### 第 {page.number} 页表格\n\n{table_text}"
        result.append(Page(page.number, text, page.has_image, page.ocr_applied))
    return result


def parse_knowledge_source(payload: bytes, filename: str) -> ParsedKnowledgeSource:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError("仅支持图片（PNG/JPG）、Markdown、PDF、TXT、DOC 和 DOCX")

    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if suffix in TEXT_EXTENSIONS:
        content = _decode_text(payload)
        return ParsedKnowledgeSource(
            content=content,
            mime_type="text/markdown" if suffix == ".md" else "text/plain",
            parser="text",
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
        temporary.write(payload)
        path = Path(temporary.name)
    try:
        if suffix == ".docx":
            return ParsedKnowledgeSource(_docx_text(path), mime_type, "docx")
        if suffix == ".doc":
            return ParsedKnowledgeSource(_legacy_doc_text(path), mime_type, "legacy-doc")

        # Knowledge uploads must not silently omit scanned pages.
        pages = load_document_text(path, use_ocr=True)
        if suffix == ".pdf" and pages:
            try:
                from cra.ingest.pdf_reader import extract_tables

                tables = extract_tables(path, [page.number for page in pages])
                pages = _pages_with_tables(pages, tables)
            except Exception:
                # Text/OCR parsing remains usable when a PDF table extractor
                # cannot understand a particular file.
                pass
        content = "\n\n".join(
            f"## 第 {page.number} 页\n\n{page.text.strip()}"
            for page in pages
            if page.text.strip()
        )
        ocr_pages = tuple(page.number for page in pages if page.ocr_applied)
        parser = ("pdf+ocr" if ocr_pages else "pdf-text") if suffix == ".pdf" else "image-ocr"
        if suffix in IMAGE_EXTENSIONS and not content.strip():
            raise RuntimeError("图片 OCR 未识别到文字；文件未入库")
        from cra.ingest.pdf_reader import ocr_backend

        backend = ocr_backend() if ocr_pages else ""
        return ParsedKnowledgeSource(content, mime_type, parser, pages=pages,
                                     ocr_backend=backend, ocr_page_numbers=ocr_pages)
    finally:
        path.unlink(missing_ok=True)
