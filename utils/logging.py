from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logging(log_file: Path | None = None, level: str = "INFO") -> None:
    logger.remove()
    logger.add(sys.stderr, level=level, enqueue=True)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(str(log_file), level="DEBUG", rotation="10 MB", retention=5, enqueue=True)
