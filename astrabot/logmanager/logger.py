"""日志管理：同时输出到控制台和按时间戳命名的日志文件"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "chat_service" / "data" / "logs"
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _ensure_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logger(name: str = "astrabot", level: int = logging.DEBUG) -> logging.Logger:
    _ensure_dir()
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = LOG_DIR / f"{name}_{timestamp}.log"
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setLevel(level)
        formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


logger = setup_logger()
