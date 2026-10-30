import asyncio
import hashlib
import io
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from cra_api import main


def configure_runtime(tmp_path: Path, monkeypatch) -> Path:
    runtime = tmp_path / "runtime"
    cache = runtime / "managed-cache"
    cache.mkdir(parents=True)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    monkeypatch.setattr(main, "MANAGED_CACHE", cache)
    return runtime


def test_legacy_upload_is_hashed_and_materialized(tmp_path, monkeypatch):
    configure_runtime(tmp_path, monkeypatch)
    payload = b"PK\x03\x04mock-xlsx"
    path, filename, checksum = asyncio.run(
        main.materialize_managed_upload(
            UploadFile(filename="case.xlsx", file=io.BytesIO(payload)),
            managed_id="",
            managed_url="",
            managed_path="",
            managed_name="",
            managed_size="",
            managed_sha256="",
            allowed_suffixes={".xlsx"},
            max_bytes=1024,
            default_name="case.xlsx",
        )
    )

    assert path.read_bytes() == payload
    assert filename == "case.xlsx"
    assert checksum == hashlib.sha256(payload).hexdigest()


def test_local_managed_upload_rejects_hash_mismatch(tmp_path, monkeypatch):
    runtime = configure_runtime(tmp_path, monkeypatch)
    file_id = str(uuid4())
    source = runtime / "managed-files" / "cra" / "case_excel" / file_id
    source.parent.mkdir(parents=True)
    source.write_bytes(b"managed-content")

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            main.materialize_managed_upload(
                None,
                managed_id=file_id,
                managed_url="",
                managed_path=str(source),
                managed_name="case.xlsx",
                managed_size=str(source.stat().st_size),
                managed_sha256="0" * 64,
                allowed_suffixes={".xlsx"},
                max_bytes=1024,
                default_name="case.xlsx",
            )
        )

    assert error.value.status_code == 422
    assert "SHA-256" in error.value.detail
