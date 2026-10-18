"""智谱 GLM-OCR（layout_parsing 接口）：扫描件、照片转文字，按页返回，表格保留行列。

接口：POST {base_url}/layout_parsing，model="glm-ocr"，file 接受 URL 或 data:<mime>;base64,...
返回：md_results（整份 Markdown）、layout_details（每页一个数组，元素含 label / content / bbox_2d）。
限制：图片 ≤ 10MB、PDF ≤ 50MB；单次页数上限文档有 30/100 两种说法，这里按 30 页一批。

合规：云端 API，文件会发送到智谱服务器。演示只用公开或模拟数据。
"""
import base64
import html
import mimetypes
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import httpx

from cra.config import get_settings
from cra.trace import log_event

MODEL = "glm-ocr"
MAX_PAGES_PER_CALL = 30
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


@dataclass
class OcrPage:
    number: int  # 原文件中的页码，从 1 开始
    text: str
    blocks: List[dict] = field(default_factory=list)  # 原始版面块：label / content / bbox_2d


@dataclass
class OcrResult:
    pages: List[OcrPage]
    markdown: str
    usage: Dict[str, int] = field(default_factory=dict)


def html_table_to_text(fragment: str) -> str:
    """把 <table> 转成「单元格用两个空格分隔、一行一行」的纯文本，和 PyMuPDF 取出的报表格式一致。"""
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", fragment, flags=re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in cells]
        rows.append("  ".join(c for c in cells if c))
    return "\n".join(r for r in rows if r) or html.unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


def block_text(block: dict) -> str:
    content = (block.get("content") or "").strip()
    if block.get("label") == "image" or not content:
        return ""
    return html_table_to_text(content) if "<table" in content.lower() else content


def to_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/pdf"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def page_ranges(page_numbers: Sequence[int], max_len: int = MAX_PAGES_PER_CALL) -> List[Tuple[int, int]]:
    """把要识别的页码合并成连续区间，每段不超过 max_len 页：[1,2,3,7,8] → [(1,3),(7,8)]。"""
    ranges: List[Tuple[int, int]] = []
    for n in sorted(set(page_numbers)):
        if ranges and n == ranges[-1][1] + 1 and n - ranges[-1][0] < max_len:
            ranges[-1] = (ranges[-1][0], n)
        else:
            ranges.append((n, n))
    return ranges


class GlmOcrClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 http: Optional[httpx.Client] = None, timeout: float = 180.0):
        settings = get_settings()
        self.api_key = api_key or settings.glm_api_key
        if not self.api_key:
            raise RuntimeError("未配置 GLM_API_KEY：请在 .env 中填写智谱开放平台的 API key。")
        self.url = (base_url or settings.glm_base_url).rstrip("/") + "/layout_parsing"
        self._http = http or httpx.Client(timeout=timeout)

    def _post(self, payload: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        resp = self._http.post(self.url, json=payload, headers=headers)
        if resp.status_code == 401:  # 部分文档示例直接传 key，不带 Bearer
            headers["Authorization"] = self.api_key
            resp = self._http.post(self.url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"GLM-OCR 调用失败（HTTP {resp.status_code}）：{resp.text[:300]}")
        return resp.json()

    def _parse_once(self, file_value: str, start: Optional[int], end: Optional[int]) -> OcrResult:
        payload = {"model": MODEL, "file": file_value}
        if start is not None:
            payload.update(start_page_id=start, end_page_id=end)
        t0 = time.time()
        data = self._post(payload)
        offset = (start or 1) - 1
        pages = []
        for i, blocks in enumerate(data.get("layout_details") or []):
            blocks = sorted(blocks or [], key=lambda b: b.get("index", 0))
            text = "\n".join(t for t in (block_text(b) for b in blocks) if t)
            pages.append(OcrPage(number=offset + i + 1, text=text, blocks=blocks))
        usage = data.get("usage") or {}
        log_event("ocr_call", {"model": MODEL, "pages": [start, end], "n_pages": len(pages),
                               "latency_s": round(time.time() - t0, 2), "usage": usage})
        return OcrResult(pages, data.get("md_results") or "", usage)

    def parse(self, path: Path, page_numbers: Optional[Sequence[int]] = None) -> OcrResult:
        """识别图片或 PDF。PDF 可只识别指定页（自动按 30 页分批）。"""
        path = Path(path)
        size = path.stat().st_size
        is_image = path.suffix.lower() in IMAGE_SUFFIXES
        limit = MAX_IMAGE_BYTES if is_image else MAX_PDF_BYTES
        if size > limit:
            raise ValueError(f"{path.name} 大小 {size / 1e6:.1f}MB，超过 GLM-OCR 限制 {limit / 1e6:.0f}MB")
        file_value = to_data_uri(path)
        if is_image:
            return self._parse_once(file_value, None, None)
        if page_numbers is None:
            import fitz
            with fitz.open(path) as doc:
                page_numbers = range(1, doc.page_count + 1)
        results = [self._parse_once(file_value, s, e) for s, e in page_ranges(page_numbers)]
        usage: Dict[str, int] = {}
        for r in results:
            for k, v in r.usage.items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
        return OcrResult([p for r in results for p in r.pages], "\n\n".join(r.markdown for r in results), usage)
