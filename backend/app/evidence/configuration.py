"""Explicit storage/key provisioning. No import-time activation or key creation."""

import os
import re
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

from app.evidence.crypto import KEY_ID_PATTERN, EnvelopeCodec
from app.evidence.errors import EvidenceUnavailable, UnsafeStorage
from app.evidence.filesystem import (
    PrivateDirectory,
    reject_ambiguous_posix_root,
    validate_private_location,
)


class FileKeyProvider:
    def __init__(self, root: Path, *, repository_root: Path):
        self.directory = PrivateDirectory(root, repository_root=repository_root)

    def get_key(self, key_id: str) -> bytes:
        if re.fullmatch(KEY_ID_PATTERN, key_id) is None:
            raise EvidenceUnavailable("Evidence key unavailable.")
        try:
            fd = self.directory.open_file(f"{key_id}.kek", os.O_RDONLY)
            try:
                material = os.read(fd, 33)
            finally:
                os.close(fd)
            if len(material) != 32:
                raise EvidenceUnavailable("Evidence key unavailable.")
            return material
        except (OSError, UnsafeStorage):
            raise EvidenceUnavailable("Evidence key unavailable.") from None

    def close(self) -> None:
        self.directory.close()


def storage_configuration(
    environment: Mapping[str, str],
) -> tuple[Path, Path, str]:
    raw = [
        environment.get(name, "")
        for name in ("LAB_EVIDENCE_ROOT", "LAB_EVIDENCE_KEY_ROOT", "LAB_EVIDENCE_ACTIVE_KEY_ID")
    ]
    if not all(raw) or re.fullmatch(KEY_ID_PATTERN, raw[2]) is None:
        raise UnsafeStorage("Evidence storage is not configured.")
    storage, keys = Path(raw[0]), Path(raw[1])
    reject_ambiguous_posix_root(storage)
    reject_ambiguous_posix_root(keys)
    if (
        not storage.is_absolute()
        or not keys.is_absolute()
        or ".." in storage.parts
        or ".." in keys.parts
        or storage.is_relative_to(keys)
        or keys.is_relative_to(storage)
    ):
        raise UnsafeStorage("Separate absolute storage and key directories are required.")
    return storage, keys, raw[2]


@contextmanager
def configured_storage(environment: Mapping[str, str], *, repository_root: Path):
    """Explicit lifetime; no settings activation merely by importing the application."""
    from app.evidence.storage import LocalEvidenceStorage

    root, key_root, active_id = storage_configuration(environment)
    validate_private_location(root, repository_root=repository_root)
    validate_private_location(key_root, repository_root=repository_root)
    provider = FileKeyProvider(key_root, repository_root=repository_root)
    try:
        provider.get_key(active_id)  # fail closed before allowing any write
        storage = LocalEvidenceStorage(
            root, EnvelopeCodec(provider, active_id), repository_root=repository_root
        )
        try:
            yield storage
        finally:
            storage.close()
    finally:
        provider.close()
