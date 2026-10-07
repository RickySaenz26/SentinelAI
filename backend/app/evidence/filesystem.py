"""Linux descriptor-relative private filesystem boundary.

Windows is deliberately fail-closed: pathlib checks cannot provide equivalent
reparse-point/ACL/race guarantees. Use the Linux container, not a Windows bind
mount. Same-UID processes, root and a compromised host remain trusted.
"""

import os
import re
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.evidence.errors import UnsafeStorage


def reject_ambiguous_posix_root(path: Path) -> None:
    # pathlib preserves '//', but the descriptor walk below always starts at '/'.
    if path.root == "//":
        raise UnsafeStorage("Ambiguous POSIX root is not allowed.")


def validate_private_location(path: Path, *, repository_root: Path) -> None:
    if sys.platform != "linux":
        raise UnsafeStorage("Private filesystem adapter requires Linux.")
    reject_ambiguous_posix_root(path)
    reject_ambiguous_posix_root(repository_root)
    if not path.is_absolute() or ".." in path.parts:
        raise UnsafeStorage("An absolute private directory is required.")
    repository = repository_root.resolve()
    if path == Path("/") or path.is_relative_to(repository) or repository.is_relative_to(path):
        raise UnsafeStorage("Storage must be outside the repository.")


class PrivateDirectory:
    def __init__(self, path: Path, *, repository_root: Path):
        validate_private_location(path, repository_root=repository_root)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open("/", flags)
        try:
            # Walk every component without following symlinks; retain a pinned fd.
            for part in path.parts[1:]:
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
                info = os.fstat(fd)
                if info.st_uid not in (0, os.geteuid()) or (
                    info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
                ):
                    raise UnsafeStorage("Untrusted directory ancestor.")
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise UnsafeStorage("Private directory ownership or permissions required.")
        except (OSError, UnsafeStorage):
            os.close(fd)
            raise UnsafeStorage("Private directory could not be opened safely.") from None
        self.fd = fd

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    @staticmethod
    def check_file(fd: int, *, maximum_links: int = 1) -> None:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or not 1 <= info.st_nlink <= maximum_links
        ):
            raise UnsafeStorage("Unsafe private file.")

    def open_file(self, name: str, flags: int, *, maximum_links: int = 1) -> int:
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) is None or name in (".", ".."):
            raise UnsafeStorage("Invalid internal object name.")
        fd = os.open(
            name, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=self.fd
        )
        try:
            self.check_file(fd, maximum_links=maximum_links)
        except UnsafeStorage:
            os.close(fd)
            raise
        return fd

    def sync(self) -> None:
        os.fsync(self.fd)

    @contextmanager
    def lock(self, *, exclusive: bool) -> Iterator[None]:
        # A new open description per acquisition also serializes separate threads.
        import fcntl

        fd = self.open_file(".evidence.lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            os.close(fd)
