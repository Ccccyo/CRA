import base64
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from cra.ingest import paddle_api_ocr, pdf_reader


def _settings(url="https://ocr.example.test/ocr", token="test-token"):
    return SimpleNamespace(paddle_ocr_api_url=url, paddle_ocr_api_token=token)


def test_paddle_api_request_and_ordered_rows(monkeypatch):
    monkeypatch.setattr(paddle_api_ocr, "get_settings", lambda: _settings())

    def handler(request):
        assert request.url.path == "/ocr"
        assert request.headers["authorization"] == "Bearer test-token"
        body = __import__("json").loads(request.content)
        assert base64.b64decode(body["file"]) == b"image-bytes"
        assert body["fileType"] == 1
        assert body["visualize"] is False
        return httpx.Response(200, json={
            "errorCode": 0,
            "result": {"ocrResults": [{"prunedResult": {
                "rec_texts": ["100", "资产"],
                "rec_boxes": [[100, 0, 130, 20], [0, 0, 30, 20]],
            }}]},
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert paddle_api_ocr.ocr_image_bytes(b"image-bytes", client=client) == "资产  100"


@pytest.mark.parametrize("url", ["http://ocr.example.test/ocr", "file:///tmp/ocr", "https://user:pass@ocr.example.test/ocr"])
def test_remote_endpoint_must_be_safe(monkeypatch, url):
    monkeypatch.setattr(paddle_api_ocr, "get_settings", lambda: _settings(url))
    with pytest.raises(RuntimeError):
        paddle_api_ocr.ocr_image_bytes(b"image")


def test_malformed_result_fails_without_leaking_response(monkeypatch):
    monkeypatch.setattr(paddle_api_ocr, "get_settings", lambda: _settings())
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"errorCode": 9, "secret": "customer text"})
    )) as client:
        with pytest.raises(RuntimeError, match="结果校验失败") as exc:
            paddle_api_ocr.ocr_image_bytes(b"image", client=client)
    assert "customer text" not in str(exc.value)


def test_mixed_pdf_requires_ocr_and_keeps_page_number(monkeypatch):
    monkeypatch.setattr(pdf_reader, "read_pages", lambda path: [
        pdf_reader.Page(1, "digital page"),
        pdf_reader.Page(2, "", has_image=True),
    ])
    monkeypatch.setattr(pdf_reader, "ocr_pages", lambda path, numbers: {2: "scanned page"})
    pages = pdf_reader.load_pdf_text(Path("mixed.pdf"), use_ocr=True)
    assert [page.number for page in pages] == [1, 2]
    assert pages[1].ocr_applied is True
    assert pages[1].text == "scanned page"


def test_empty_ocr_page_rejects_partial_index(monkeypatch):
    monkeypatch.setattr(pdf_reader, "read_pages", lambda path: [
        pdf_reader.Page(1, "digital page"),
        pdf_reader.Page(2, "", has_image=True),
    ])
    monkeypatch.setattr(pdf_reader, "ocr_pages", lambda path, numbers: {2: ""})
    with pytest.raises(RuntimeError, match="文件未入库"):
        pdf_reader.load_pdf_text(Path("mixed.pdf"), use_ocr=True)
