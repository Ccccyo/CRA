"""年报（10-K）切段：按 Item 章节 → 附注（Note）→ 句子打包，每段带公司、年份、章节标签。

只保留和信用分析有关的章节：
- Item 1A 风险因素、Item 3 法律诉讼、Item 7 管理层讨论（流动性、负债、展望）、Item 7A 市场风险
- Item 8 财务报表和附注（债务、契约、承诺与或有事项、期后事项都在附注里）

难点和处理：
- 目录页也列着所有 Item：同一页出现 3 个以上 Item 标题的当作目录页，跳过
- 正文里的交叉引用（如「Item 7. Other Matters…in Management's Discussion」）：标题后面的文字必须是该章节的标准名称
- 网页转的 PDF 一行一断：先把行拼成整段文字，再按句子打包成约 900 字的段，相邻段重叠 1 句
"""
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from cra.ingest.chunker import Chunk

# Item 编号 → 标准标题的开头（小写），用来区分真标题和交叉引用
ITEM_TITLES = {
    "1": "business", "1a": "risk factors", "1b": "unresolved", "1c": "cybersecurity", "2": "properties",
    "3": "legal proceedings", "4": "mine safety", "5": "market for", "6": "reserved", "7": "management",
    "7a": "quantitative", "8": "financial statements", "9": "changes in", "9a": "controls", "9b": "other information",
    "9c": "disclosure regarding", "10": "directors",
}
KEEP = {"1a": "Item 1A 风险因素", "3": "Item 3 法律诉讼", "7": "Item 7 管理层讨论与分析",
        "7a": "Item 7A 市场风险", "8": "Item 8 财务报表与附注"}
ITEM_RE = re.compile(r"^item\s*(\d{1,2}[a-c]?)\s*[\.:]\s*(.*)$", re.I)
# 附注标题的几种写法：「NOTE 12 – DEBT」「Note 12: Debt」「13. Debt」，或编号单独一行「1.」下一行是标题
NOTE_RE = re.compile(r"^(?:[Nn][Oo][Tt][Ee][Ss]?\s+)?(\d{1,2})\s*[\.:\-–—]\s*([A-Z][^\.]{2,80})$")
NOTE_NUM_ONLY = re.compile(r"^(\d{1,2})\.$")
ITEM_ORDER = list(ITEM_TITLES)
TOC_MIN_ITEMS = 8  # 一页出现 8 个以上 Item 标题才算目录页（真实正文里也会有一页挤 5 个短章节）
AUDIT_REPORT = "report of independent registered public accounting firm"
CHUNK_CHARS = 900


@dataclass
class Section:
    item: str  # 如 "7a"
    title: str
    start_page: int
    end_page: int
    lines: List[Tuple[int, str]]  # (页码, 行)


def _clean_lines(pages: Sequence[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """去掉页眉页脚：「Table of Contents」、纯页码、在很多页重复出现的行（如公司名页眉）。"""
    per_page = [{ln.strip() for ln in text.splitlines() if ln.strip()} for _, text in pages]
    counts = Counter(ln for lines in per_page for ln in lines)
    repeated = {ln for ln, c in counts.items() if c > max(5, len(pages) * 0.3) and len(ln) < 60}
    out = []
    for number, text in pages:
        for ln in text.splitlines():
            s = ln.replace("\xa0", " ").strip()
            if not s or s.lower() == "table of contents" or re.fullmatch(r"[\divxlc\-–—\s]{1,8}", s, re.I):
                continue
            if s in repeated or ln.strip() in repeated:
                continue
            out.append((number, s))
    return out


def find_sections(pages: Sequence[Tuple[int, str]]) -> List[Section]:
    lines = _clean_lines(pages)
    raw = [(i, m.group(1).lower(), m.group(2).strip()) for i, (_, s) in enumerate(lines)
           if len(s) < 140 and (m := ITEM_RE.match(s))]
    by_page = Counter(lines[i][0] for i, _, _ in raw)
    heads = []
    for i, item, rest in raw:
        if by_page[lines[i][0]] >= TOC_MIN_ITEMS or item not in ITEM_TITLES:  # 目录页
            continue
        title = rest or (lines[i + 1][1] if i + 1 < len(lines) else "")
        if not title.lower().lstrip("’'\" ").startswith(ITEM_TITLES[item]):  # 标题名对不上：交叉引用
            continue
        if heads and ITEM_ORDER.index(item) <= ITEM_ORDER.index(heads[-1][1]):  # 顺序倒退：正文里的交叉引用
            continue
        heads.append((i, item, title))
    sections = []
    for k, (i, item, title) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        body = lines[i + 1:end]
        if item == "8" and sum(len(t) for _, t in body) < 2000:
            body = _statements_at_back(lines, end)  # 只有一句「见 F-1 页」：报表和附注在年报最后
        if item in KEEP and body:
            sections.append(Section(item, KEEP[item], body[0][0], body[-1][0], body))
    return sections


def _statements_at_back(lines: List[Tuple[int, str]], after: int) -> List[Tuple[int, str]]:
    """Item 8 写「报表见 F-1 页起」时，从审计报告开始到文末就是报表和附注。"""
    start = next((i for i in range(after, len(lines)) if lines[i][1].lower().startswith(AUDIT_REPORT)), None)
    return lines[start:] if start is not None else []


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[\.\?!;])\s+(?=[A-Z\(“\"])", text)
    return [p.strip() for p in parts if p.strip()]


def _pack(lines: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """把行拼起来按句子打包成约 CHUNK_CHARS 字的段，相邻段重叠 1 句。返回 (起始页, 文字)。"""
    if not lines:
        return []
    sents: List[Tuple[int, str]] = []
    for page, text in _join_lines(lines):
        sents += [(page, s) for s in _sentences(text)]
    out, buf, start = [], [], None
    for page, sent in sents:
        if buf and sum(len(s) for s in buf) + len(sent) > CHUNK_CHARS:
            out.append((start, " ".join(buf)))
            buf, start = buf[-1:], page
        if start is None:
            start = page
        buf.append(sent)
    if buf and (not out or len(buf) > 1):
        out.append((start, " ".join(buf)))
    return out


def _join_lines(lines: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """网页转 PDF 一行一断：同一页的行拼成一段文字（跨页时另起一段，便于记页码）。"""
    out, page, buf = [], None, []
    for p, s in lines:
        if page is not None and p != page:
            out.append((page, " ".join(buf)))
            buf = []
        page = p
        buf.append(s)
    if buf:
        out.append((page, " ".join(buf)))
    return out


def chunk_filing(path: Path, ticker: str, company: str, fiscal_year: str) -> List[Chunk]:
    from cra.ingest.pdf_reader import read_pages

    pages = [(p.number, p.text) for p in read_pages(path)]
    chunks: List[Chunk] = []
    source = f"年报/{ticker.upper()} 10-K FY{fiscal_year}"
    for sec in find_sections(pages):
        groups: List[Tuple[Optional[str], List[Tuple[int, str]]]] = [(None, [])]
        last_note = 0
        for k, (page, s) in enumerate(sec.lines):
            num, title = None, ""
            if sec.item == "8" and len(s) < 100:
                m = NOTE_RE.match(s)
                if m:
                    num, title = int(m.group(1)), m.group(2)
                elif NOTE_NUM_ONLY.match(s) and k + 1 < len(sec.lines) and sec.lines[k + 1][1][:1].isupper():
                    num, title = int(s[:-1]), sec.lines[k + 1][1][:80]
            if num == last_note + 1:  # 附注编号必须连续，避免把普通的列表编号当成附注
                last_note = num
                groups.append((f"Note {num} {title}".strip(), []))
            groups[-1][1].append((page, s))
        for note, lines in groups:
            label = f"{sec.title} > {note}" if note else sec.title
            for start, text in _pack(lines):
                n = len(chunks)
                head = f"[{company} {fiscal_year} 年报 | {label}]"
                chunks.append(Chunk(id=f"10k#{ticker.lower()}-{fiscal_year}-{n}", text=f"{head}\n{text}",
                                    source=source, section=label, page=start,
                                    meta={"ticker": ticker.upper(), "company": company, "fiscal_year": fiscal_year,
                                          "item": sec.item, "tier": "年报"}))
    return chunks
