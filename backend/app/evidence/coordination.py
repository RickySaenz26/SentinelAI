"""Local exclusion shared by writers and maintenance; acquire before any DB session."""

import os
from contextlib import contextmanager

from app.evidence.errors import EvidenceError


class EvidenceBusy(EvidenceError):
    """Another local operation owns the coordination lock; retry later."""


@contextmanager
def coordinate(storage):
    import fcntl

    fd = storage.directory.open_file(".evidence.coordinator.lock", os.O_RDWR | os.O_CREAT)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EvidenceBusy("Local evidence operation in progress.") from None
        yield
    finally:
        os.close(fd)
