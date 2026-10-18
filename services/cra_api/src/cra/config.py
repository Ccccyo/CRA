"""集中管理路径和模型配置。值来自环境变量或项目根目录的 .env 文件。"""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """读取 .env（不覆盖已存在的环境变量），避免额外依赖。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    cra_api_shared_secret: str
    database_url: str
    deepseek_api_key: str
    deepseek_base_url: str
    chat_model: str
    reasoner_model: str
    embed_model: str
    rerank_model: str
    glm_api_key: str
    glm_base_url: str
    ocr_backend: str  # auto / glm / paddle / none
    data_dir: Path
    eval_dir: Path
    reports_dir: Path
    logs_dir: Path
    index_dir: Path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # The CRA service lives under services/cra_api while the shared local
    # environment file belongs to the web project root.
    project_root = ROOT.parent.parent
    _load_dotenv(project_root / ".env.local")
    _load_dotenv(project_root / ".env")
    _load_dotenv(ROOT / ".env")
    return Settings(
        cra_api_shared_secret=os.environ.get("CRA_API_SHARED_SECRET", ""),
        database_url=os.environ.get("DATABASE_URL", ""),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        chat_model=os.environ.get("CHAT_MODEL", "deepseek-chat"),
        reasoner_model=os.environ.get("REASONER_MODEL", "deepseek-reasoner"),
        embed_model=os.environ.get("EMBED_MODEL", "BAAI/bge-m3"),
        rerank_model=os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
        glm_api_key=os.environ.get("GLM_API_KEY", ""),
        glm_base_url=os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
        ocr_backend=os.environ.get("OCR_BACKEND", "auto").lower(),
        data_dir=ROOT / "data",
        eval_dir=ROOT / "eval",
        reports_dir=ROOT / "reports",
        logs_dir=ROOT / "logs",
        index_dir=ROOT / "index",
    )
