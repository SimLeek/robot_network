import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

_root = None

def setup_logging():
    """Call once from any entry point. Idempotent."""
    global _root
    if _root is not None:
        return _root
    _root = logging.getLogger()
    if _root.handlers:
        return _root
    _root.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s')

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    _root.addHandler(ch)

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    fh = RotatingFileHandler(log_dir / "robonet.log", maxBytes=10*1024*1024, backupCount=10)
    fh.setFormatter(formatter)
    _root.addHandler(fh)

    logging.getLogger("robonet").info("=== Robonet Logging Initialized (DEBUG/verbose) ===")
    return _root