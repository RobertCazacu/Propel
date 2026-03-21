"""
Application-level logger.
Writes to data/logs/app.log (rotating, max 2MB x 3 files) + stderr.
Usage:
    from core.app_logger import get_logger
    log = get_logger(__name__)
    log.warning("...")
"""
import logging
import logging.handlers
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "data" / "logs"
_configured = False


def get_logger(name: str = "marketplace") -> logging.Logger:
    global _configured
    if not _configured:
        _configured = True
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        root = logging.getLogger("marketplace")
        root.setLevel(logging.DEBUG)

        if not root.handlers:
            # Rotating file — 2 MB x 3
            fh = logging.handlers.RotatingFileHandler(
                LOGS_DIR / "app.log",
                maxBytes=2 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            root.addHandler(fh)

            # Console — only WARNING and above
            ch = logging.StreamHandler()
            ch.setLevel(logging.WARNING)
            ch.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
            root.addHandler(ch)

    return logging.getLogger(name)
