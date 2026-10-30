"""把文档切成可检索的小段。每段都记住出处（文件、章节、页码），回答时才能标注来源。"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from cra.ingest.pdf_reader import Page


@dataclass
class Chunk:
    id: str
    text: str
    source: str  # 文件名
    section: str = ""  # Markdown 标题路径，如 "错误码 > EAPI030"
    page: Optional[int] = None
    meta: Dict[str, str] = field(default_factory=dict)  # 检索过滤用的标签，如 ticker、fiscal_year、item

    @property
    def citation(self) -> str:
        if self.page and self.section:  # 年报：章节 + 页码
            return f"{self.source}（{self.section}，第{self.page}页）"
        where = f"第{self.page}页" if self.page else self.section
        return f"{self.source}（{where}）" if where else self.source


def _split_long(
    text: str, max_chars: int, overlap_chars: int = 100
) -> List[str]:
    """Split at readable boundaries and retain a small context overlap."""
    content = "\n\n".join(
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    )
    if not content:
        return []
    overlap_chars = max(0, min(overlap_chars, max_chars // 3))
    parts: List[str] = []
    start = 0
    while start < len(content):
        hard_end = min(start + max_chars, len(content))
        end = hard_end
        if hard_end < len(content):
            window = content[start : hard_end + 1]
            cut = max(
                window.rfind(mark)
                for mark in ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", " ")
            )
            if cut >= max_chars // 2:
                end = start + cut + 1
        part = content[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(content):
            break
        start = max(end - overlap_chars, start + 1)
        while start < len(content) and content[start].isspace():
            start += 1
    return parts


def chunk_markdown(
    text: str,
    source: str,
    max_chars: int = 800,
    overlap_chars: int = 100,
) -> List[Chunk]:
    """按 Markdown 标题切段；标题路径写进 section，并作为段落开头帮助检索。"""
    chunks: List[Chunk] = []
    heading_path: List[str] = []
    body: List[str] = []

    def flush() -> None:
        content = "\n".join(body).strip()
        if not content:
            return
        section = " > ".join(heading_path)
        body_limit = max(200, max_chars - len(section) - (1 if section else 0))
        for piece in _split_long(content, body_limit, overlap_chars):
            chunks.append(
                Chunk(id=f"{source}#{len(chunks)}", text=f"{section}\n{piece}" if section else piece,
                      source=source, section=section)
            )

    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            flush()
            body = []
            level = len(m.group(1))
            heading_path = heading_path[: level - 1] + [m.group(2).strip()]
        else:
            body.append(line)
    flush()
    return chunks


def chunk_pages(
    pages: Sequence[Page],
    source: str,
    max_chars: int = 1000,
    overlap_chars: int = 120,
) -> List[Chunk]:
    chunks: List[Chunk] = []
    for page in pages:
        for piece in _split_long(page.text, max_chars, overlap_chars):
            chunks.append(Chunk(id=f"{source}#p{page.number}-{len(chunks)}", text=piece,
                                source=source, page=page.number))
    return chunks


def load_markdown_dir(directory: Path, prefix: str = "") -> List[Chunk]:
    """prefix 标在出处前面区分层级，如「公开资料/」。"""
    chunks: List[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(chunk_markdown(path.read_text(encoding="utf-8"), source=prefix + path.name))
    return chunks
