from __future__ import annotations

import os
from pathlib import Path


class SchedulerLock:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.file: object | None = None
        self.acquired = False
        self._windows = os.name == "nt"

    def __enter__(self) -> "SchedulerLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = open(self.path, "a+b")
        self.file = file
        if self._windows:
            import msvcrt

            file.seek(0, os.SEEK_END)
            if file.tell() == 0:
                file.write(b"0")
                file.flush()
            file.seek(0)
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                self.acquired = True
            except OSError:
                self.acquired = False
            return self

        import fcntl

        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            self.acquired = False
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        file = self.file
        if file is None:
            return
        try:
            if self.acquired:
                if self._windows:
                    import msvcrt

                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        finally:
            file.close()
            self.file = None
            self.acquired = False
