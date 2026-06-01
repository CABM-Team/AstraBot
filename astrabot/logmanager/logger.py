"""日志管理：使用 NoneBot 官方 loguru logger，统一日志格式，同时输出到文件"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nonebot import logger

LOG_DIR = Path(__file__).resolve().parent.parent / "chat_service" / "data" / "logs"


def _setup_file_sink():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = LOG_DIR / f"astrabot_{timestamp}.log"
    logger.add(
        file_path,
        encoding="utf-8",
        rotation="10 MB",
        retention="30 days",
        level="DEBUG",
    )


_setup_file_sink()
