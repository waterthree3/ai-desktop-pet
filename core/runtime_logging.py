import atexit
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import TextIO


_INSTALLED_LOG_PATH: Path | None = None


class _TeeStream:
    def __init__(self, *streams: TextIO):
        self._streams = streams
        self._lock = threading.Lock()
        self.encoding = getattr(streams[0], "encoding", "utf-8") if streams else "utf-8"

    def write(self, data):
        with self._lock:
            for stream in self._streams:
                stream.write(data)
            for stream in self._streams:
                stream.flush()
        return len(data)

    def flush(self):
        with self._lock:
            for stream in self._streams:
                stream.flush()

    def isatty(self):
        return any(getattr(stream, "isatty", lambda: False)() for stream in self._streams)


def install_session_logging(root_dir: str | Path, folder_name: str = "log") -> Path:
    global _INSTALLED_LOG_PATH
    if _INSTALLED_LOG_PATH is not None:
        return _INSTALLED_LOG_PATH

    root = Path(root_dir)
    log_dir = root / folder_name
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{datetime.now():%Y%m%d_%H%M%S}.log"

    log_file = log_path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = _TeeStream(original_stdout, log_file)
    sys.stderr = _TeeStream(original_stderr, log_file)

    def _cleanup() -> None:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            log_file.flush()
            log_file.close()

    atexit.register(_cleanup)
    _INSTALLED_LOG_PATH = log_path
    print(f"[LOG] Session log file: {log_path}", flush=True)
    return log_path
