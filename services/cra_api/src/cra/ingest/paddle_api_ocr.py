"""Client for an explicitly configured PaddleOCR 3.x pipeline serving endpoint.

Only page images are sent. This keeps PDF pagination and citations under our
control and avoids the serving pipeline's PDF page-count defaults.
"""
import base64
from pathlib import Path
from urllib.parse import urlparse

import httpx

from cra.config import get_settings
from cra.ingest.paddle_ocr import RENDER_DPI, TextBox, group_rows


def _endpoint() -> tuple[str, str]:
    settings = get_settings()
    url = settings.paddle_ocr_api_url.rstrip("/")
    parsed = urlparse(url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if not url or parsed.scheme not in ({"http", "https"} if local else {"https"}):
        raise RuntimeError("PaddleOCR API 地址未配置或不安全；远程服务必须使用 HTTPS")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("PaddleOCR API 地址格式无效")
    return (url if parsed.path.endswith("/ocr") else f"{url}/ocr"), settings.paddle_ocr_api_token


def ocr_image_bytes(payload: bytes, *, client: httpx.Client | None = None) -> str:
    url, token = _endpoint()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = {"file": base64.b64encode(payload).decode("ascii"), "fileType": 1, "visualize": False}
    try:
        if client is None:
            with httpx.Client(timeout=90, trust_env=False, follow_redirects=False) as session:
                response = session.post(url, json=request, headers=headers)
        else:
            response = client.post(url, json=request, headers=headers)
        response.raise_for_status()
        data = response.json()
        if data.get("errorCode") != 0:
            raise ValueError("OCR 服务返回失败状态")
        results = data["result"]["ocrResults"]
        if len(results) != 1:
            raise ValueError("OCR 服务返回页数异常")
        result = results[0]["prunedResult"]
        texts, boxes = result["rec_texts"], result["rec_boxes"]
        if not isinstance(texts, list) or not isinstance(boxes, list) or len(texts) != len(boxes):
            raise ValueError("OCR 服务返回的文字框不完整")
        parsed_boxes = [TextBox(str(text), *(float(value) for value in box)) for text, box in zip(texts, boxes) if len(box) == 4]
        if len(parsed_boxes) != len(texts):
            raise ValueError("OCR 服务返回的坐标格式无效")
        text = group_rows(parsed_boxes)
        if not text.strip():
            raise ValueError("OCR 未识别到文字")
        return text
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        # Never include the response body: it may contain customer document text.
        raise RuntimeError("PaddleOCR API 调用或结果校验失败；文件未入库") from exc


def ocr_image(path: Path) -> str:
    return ocr_image_bytes(Path(path).read_bytes())


def ocr_pdf_page(path: Path, page_number: int) -> str:
    import fitz

    with fitz.open(path) as document:
        if page_number < 1 or page_number > len(document):
            raise ValueError("PDF 页码超出范围")
        image = document[page_number - 1].get_pixmap(dpi=RENDER_DPI).tobytes("png")
    return ocr_image_bytes(image)
