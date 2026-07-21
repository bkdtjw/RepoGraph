"""RepoGraph 配置（pydantic-settings，全部可调项集中于此，文档 §10.1）。"""
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 抽取范围（§4.1）：排除规则配置化
    exclude_dirs: tuple[str, ...] = ("tests", "migrations", ".git", "__pycache__",
                                     ".pytest_cache", ".venv", "node_modules")
    src_roots: tuple[str, ...] = ("src",)

    # 端点识别（§4.3）：装饰器模式列表，配置化
    endpoint_patterns: tuple[str, ...] = (
        r"^(app|router)\.(get|post|put|delete|patch|websocket)$",
    )

    # 语义抽取（§5）
    semantic_batch_size: int = 20            # commit message 每批条数
    semantic_confidence_min: float = 0.6
    llm_backend: str = "grok-cli"            # 本地 grok CLI headless（-p 单轮模式）
    grok_exe: str = r"C:\Users\nirvana\.grok\downloads\grok-windows-x86_64.exe"
    grok_timeout_s: int = 300

    # 输出
    output_dir: str = "output"

    model_config = {"env_prefix": "REPOGRAPH_", "extra": "ignore"}


settings = Settings()
