"""读 PDF：按页取文字、定位财报页、取表格、扫描件 OCR。

年报动辄两三百页，只把三大报表所在的几页交给大模型：省钱，也避开上下文长度限制。
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# 三大报表的标题关键词（中、英、西、葡），用来定位报表页
STATEMENT_KEYWORDS = [
    "合并资产负债表", "合并利润表", "合并现金流量表", "资产负债表", "利润表",
    "consolidated balance sheet", "consolidated statements of operations",
    "consolidated statements of income", "consolidated statement of financial position",
    "consolidated statements of cash flows", "balance sheet", "income statement",
    "estado de situación financiera", "estado de resultados", "balanço patrimonial",
    "demonstração do resultado",
]

MIN_TEXT_CHARS = 50  # 一页文字少于这个数，又有图片，才算扫描页


@dataclass
class Page:
    number: int  # 从 1 开始，和人看到的页码一致
    text: str
    has_image: bool = False  # 页面上有图片（扫描件每页都是一张图）

    @property
    def needs_ocr(self) -> bool:
        """文字很少 **且** 有图片才需要 OCR。

        坏例子：网页转的 10-K 里有几十页只有页码的空白页，曾被当成扫描页逐页 OCR（Dow 57 页，约 14 分钟）。
        """
        return len(self.text.strip()) < MIN_TEXT_CHARS and self.has_image


def read_pages(path: Path) -> List[Page]:
    import fitz  # PyMuPDF

    with fitz.open(path) as doc:
        return [Page(number=i + 1, text=page.get_text("text"), has_image=bool(page.get_images()))
                for i, page in enumerate(doc)]


# 页眉里的报表标题（英文 10-K 写法很多：Statements of Earnings / Financial Position / Balance Sheets…）
TITLE_RE = re.compile(r"balance sheets?|statements? of (?:consolidated )?(?:operations|income|earnings|financial position"
                      r"|cash flows?)|income statements?|资产负债表|利润表|现金流量表|estado de (?:situación|resultados)"
                      r"|balanço patrimonial|demonstração do resultado")
# 报表正文的科目行：没有标题的续页（资产负债表第 2 页、现金流量表第 2 页）靠这些认出来
ANCHORS = [
    "total assets", "total current assets", "total current liabilities", "total liabilities",
    "total equity", "stockholders' equity", "stockholders’ equity", "shareholders' equity", "shareholders’ equity",
    "net cash provided by", "operating activities", "investing activities", "financing activities",
    "cost of sales", "cost of goods sold", "net sales", "gross profit", "net income", "net earnings",
    "资产总计", "负债合计", "流动资产合计", "流动负债合计", "营业收入", "营业成本", "净利润", "经营活动产生的现金流量净额",
]
HEADER_LINES = 8  # 标题一般在页面前几行（前面可能有「Table of Contents」和公司名）


def find_statement_pages(pages: Sequence[Page], top_n: int = 6) -> List[Page]:
    """给每页打分：页眉有报表标题 + 正文有科目行 + 数字密度；附注页、目录页扣分。返回分数最高的几页（按页码排序）。"""
    scored = []
    prev_title_only = False  # 网页转 PDF 时，标题有时单独占一页，表格在下一页
    for page in pages:
        lower = page.text.lower()
        lines = [ln.strip() for ln in lower.splitlines() if ln.strip()]
        header = " ".join(lines[:HEADER_LINES])
        title = bool(TITLE_RE.search(header)) or prev_title_only
        prev_title_only = bool(TITLE_RE.search(header)) and len(lines) < 10
        anchors = sum(1 for a in ANCHORS if a in lower)
        keyword_hits = sum(1 for kw in STATEMENT_KEYWORDS if kw in lower)
        if not title and anchors < 3 and keyword_hits == 0:
            continue
        numbers = len(re.findall(r"\(?\d[\d,\.]{2,}\)?", page.text))
        score = 30 * title + 3 * min(anchors, 8) + min(keyword_hits, 3) + min(numbers, 100) / 10
        if any(w in header for w in ("notes to", "index to", "report of independent", "附注", "目录")):
            score -= 40  # 附注、报表目录、审计报告都会提到报表名，但不是报表本身
        if "comprehensive income" in header and "earnings" not in header:
            score -= 15  # 综合收益表对 CRA 用处不大
        scored.append((score, page))
    best = sorted(scored, key=lambda x: x[0], reverse=True)[:top_n]
    return sorted((p for _, p in best), key=lambda p: p.number)


def extract_tables(path: Path, page_numbers: Sequence[int]) -> Dict[int, List[List[List[str]]]]:
    """用 pdfplumber 取指定页的表格：{页码: [表格[行[单元格]]]}。"""
    import pdfplumber

    tables: Dict[int, List[List[List[str]]]] = {}
    with pdfplumber.open(path) as pdf:
        for n in page_numbers:
            raw = pdf.pages[n - 1].extract_tables()
            tables[n] = [[[cell or "" for cell in row] for row in table] for table in raw]
    return tables


def ocr_backend() -> str:
    """当前可用的 OCR：paddle（本地，默认优先）/ glm（智谱云端）/ none。"""
    import importlib.util

    from cra.config import get_settings

    s = get_settings()
    has_glm = bool(s.glm_api_key)
    has_paddle = importlib.util.find_spec("paddleocr") is not None
    if s.ocr_backend == "glm":
        return "glm" if has_glm else "none"
    if s.ocr_backend == "paddle":
        return "paddle" if has_paddle else "none"
    if s.ocr_backend == "none":
        return "none"
    return "paddle" if has_paddle else ("glm" if has_glm else "none")


NO_OCR_MSG = "没有可用的 OCR：安装 PaddleOCR（pip install paddlepaddle paddleocr），或在 .env 填写 GLM_API_KEY"


def ocr_pages(path: Path, page_numbers: Sequence[int], backend: Optional[str] = None) -> Dict[int, str]:
    """识别指定页，返回 {页码: 文字}。"""
    backend = backend or ocr_backend()
    if backend == "paddle":
        from cra.ingest.paddle_ocr import ocr_pdf_page

        return {n: ocr_pdf_page(path, n) for n in page_numbers}
    if backend == "glm":
        from cra.ingest.glm_ocr import GlmOcrClient

        return {p.number: p.text for p in GlmOcrClient().parse(path, page_numbers).pages}
    raise RuntimeError(NO_OCR_MSG)


def load_pdf_text(path: Path, use_ocr: Optional[bool] = None) -> List[Page]:
    """读全部页。

    use_ocr=None（默认，自动）：只对文字太少的页（扫描页）做 OCR，前提是有可用的 OCR；
    use_ocr=True：同上，但没有 OCR 时报错；use_ocr=False：不做 OCR，扫描页保留空文字。
    """
    pages = read_pages(path)
    scanned = [p.number for p in pages if p.needs_ocr]
    if not scanned or use_ocr is False:
        return pages
    if use_ocr is None and ocr_backend() == "none":
        return pages
    texts = ocr_pages(path, scanned)
    return [Page(p.number, texts[p.number], p.has_image) if p.number in texts else p for p in pages]


SCANNED_SHARE = 0.3  # 扫描页占比达到这个比例，才当作扫描件走 OCR


def load_statement_pages(path: Path, use_ocr: Optional[bool] = None) -> List[Page]:
    """抽取用：只把报表所在的几页读出来交给模型。

    - 有文字层的 PDF（如 10-K）：直接在文字里找报表页，不做 OCR，几秒读完
    - 扫描件（扫描页占比 ≥ 30%）或照片：OCR 扫描页后再找（整本扫描年报仍需逐页 OCR，较慢）
    - 判断用占比而不是「文字层里找没找到」：扫描年报的封面常有文字层，可能被误当成报表页
    """
    path = Path(path)
    if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        pages = load_document_text(path, use_ocr)
    else:
        pages = read_pages(path)
        scanned_share = sum(p.needs_ocr for p in pages) / max(len(pages), 1)
        if use_ocr or (use_ocr is None and scanned_share >= SCANNED_SHARE):
            pages = load_pdf_text(path, use_ocr)
    found = find_statement_pages([p for p in pages if not p.needs_ocr])
    return found if found else _short_document(pages)


def _short_document(pages: Sequence[Page]) -> List[Page]:
    """拍照的单页报表可能没有完整标题：三页以内的文件整份交给模型。"""
    return [p for p in pages if p.text.strip()] if len(pages) <= 3 else []


def load_document_text(path: Path, use_ocr: Optional[bool] = None) -> List[Page]:
    """PDF 走 load_pdf_text；照片（JPG/PNG）整张 OCR，作为第 1 页。"""
    path = Path(path)
    if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        backend = ocr_backend()
        if backend == "paddle":
            from cra.ingest.paddle_ocr import ocr_image

            return [Page(1, ocr_image(path))]
        if backend == "glm":
            from cra.ingest.glm_ocr import GlmOcrClient

            return [Page(p.number, p.text) for p in GlmOcrClient().parse(path).pages]
        raise RuntimeError(NO_OCR_MSG)
    return load_pdf_text(path, use_ocr)
