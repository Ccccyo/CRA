"""本地 PaddleOCR：扫描页、照片转文字，文件不出本机。

PaddleOCR 返回的是零散的文字框。财报是表格，必须按坐标把同一水平线的文字框合成一行、再从左到右排列，
否则「Inventarios」「241,000」「228,000」会被拆到不同行，后面的原文核对就对不上。
"""
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import List, Sequence

RENDER_DPI = 200  # PDF 页转图片的分辨率：太低小字识别差，太高变慢


@dataclass
class TextBox:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def group_rows(boxes: Sequence[TextBox], tolerance: float = 0.5) -> str:
    """按纵坐标把文字框聚成行：中心高度差小于「行高中位数 × tolerance」算同一行；行内按横坐标排序，用两个空格分隔。"""
    boxes = [b for b in boxes if b.text.strip()]
    if not boxes:
        return ""
    limit = median(b.height for b in boxes) * tolerance
    rows: List[List[TextBox]] = []
    for b in sorted(boxes, key=lambda b: b.yc):
        if rows and abs(b.yc - sum(x.yc for x in rows[-1]) / len(rows[-1])) <= limit:
            rows[-1].append(b)
        else:
            rows.append([b])
    return "\n".join("  ".join(b.text.strip() for b in sorted(r, key=lambda b: b.x0)) for r in rows)


@lru_cache(maxsize=1)
def _engine():
    try:
        from paddleocr import PaddleOCR
    except ImportError as e:
        raise ImportError("本地 OCR 需要：pip install paddlepaddle paddleocr") from e
    # PaddleOCR 3.x：用轻量 mobile 模型并限制检测图尺寸。
    # 实测（x86 Python 经 Rosetta，无 MKL-DNN）：server 模型识别一页内存峰值 17.7GB 后段错误；
    # mobile 模型 + 最长边 1280 每页约 15 秒、峰值约 8GB，测试扫描件数字全部识别正确。
    # 关掉文档方向/去弯曲等额外模型，扫描财报用不上。
    try:
        return PaddleOCR(lang="ch", text_detection_model_name="PP-OCRv5_mobile_det",
                         text_recognition_model_name="PP-OCRv5_mobile_rec",
                         use_doc_orientation_classify=False, use_doc_unwarping=False,
                         use_textline_orientation=False, text_det_limit_side_len=1280, text_det_limit_type="max")
    except (TypeError, ValueError):  # 2.x 不认识上面的参数
        return PaddleOCR(lang="ch", use_angle_cls=False, show_log=False)


def _boxes_from_result(result) -> List[TextBox]:
    """兼容 3.x（predict → rec_texts / rec_boxes）和 2.x（ocr → [[poly, (text, score)], ...]）。"""
    boxes: List[TextBox] = []
    for res in result or []:
        data = res
        if not isinstance(data, dict) and hasattr(res, "json"):
            data = res.json.get("res", res.json)
        if isinstance(data, dict) and "rec_texts" in data:
            for text, box in zip(data["rec_texts"], data["rec_boxes"]):
                x0, y0, x1, y1 = (float(v) for v in box)
                boxes.append(TextBox(text, x0, y0, x1, y1))
        else:  # 2.x：res 是一页的行列表
            for line in res or []:
                poly, (text, _score) = line[0], line[1]
                xs, ys = [p[0] for p in poly], [p[1] for p in poly]
                boxes.append(TextBox(text, min(xs), min(ys), max(xs), max(ys)))
    return boxes


def ocr_image(path: Path) -> str:
    engine = _engine()
    if hasattr(engine, "predict"):
        result = engine.predict(str(path))
    else:
        result = engine.ocr(str(path))
    return group_rows(_boxes_from_result(result))


def ocr_pdf_page(path: Path, page_number: int) -> str:
    import fitz

    with fitz.open(path) as doc, tempfile.TemporaryDirectory() as tmp:
        image_path = Path(tmp) / f"p{page_number}.png"
        doc[page_number - 1].get_pixmap(dpi=RENDER_DPI).save(image_path)
        return ocr_image(image_path)
