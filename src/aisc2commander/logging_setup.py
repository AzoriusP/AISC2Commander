from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(log_dir: Path, verbose: bool = False) -> Path:
    """Configure readable console logs and a detailed rotating debug log."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "aisc2commander.log"

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(
        logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s | %(message)s", "%H:%M:%S")
    )

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=8 * 1024 * 1024,
        backupCount=4,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s "
            "[%(threadName)s] %(filename)s:%(lineno)d | %(message)s",
            "%Y-%m-%dT%H:%M:%S",
        )
    )

    root.addHandler(console)
    root.addHandler(file_handler)
    logging.captureWarnings(True)
    return log_path.resolve()

