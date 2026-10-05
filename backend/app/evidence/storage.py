"""Exclusive ciphertext publication and conservative local recovery, without DB.

Never promote an orphan by age. A receipt is a trusted internal operation handle,
not an authorization token. This adapter does not establish tenant authority.
"""

import hmac
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from app.evidence.contracts import EvidenceContext, LabAttestation
from app.evidence.crypto import MAX_ENVELOPE_BYTES, EnvelopeCodec, ciphertext_digest
from app.evidence.errors import EvidenceUnavailable, ObjectExists, StorageInterrupted, UnsafeStorage
from app.evidence.filesystem import PrivateDirectory


@dataclass(frozen=True)
class ObjectReceipt:
    object_id: UUID
    context: EvidenceContext
    envelope_sha256: str


@dataclass(frozen=True)
class PreparedObject:
    receipt: ObjectReceipt
    encrypted: bytes = field(repr=False)


class EvidenceStorage(Protocol):
    def prepare(self, context: EvidenceContext, document: LabAttestation) -> PreparedObject: ...
    def write(self, prepared: PreparedObject) -> ObjectReceipt: ...
    def read(self, receipt: ObjectReceipt) -> LabAttestation: ...


class LocalEvidenceStorage:
    def __init__(self, root: Path, codec: EnvelopeCodec, *, repository_root: Path):
        self.directory = PrivateDirectory(root, repository_root=repository_root)
        self.codec = codec

    def close(self) -> None:
        self.directory.close()

    def prepare(self, context: EvidenceContext, document: LabAttestation) -> PreparedObject:
        object_id = uuid4()
        encrypted = self.codec.seal(object_id, context, document)
        return PreparedObject(
            ObjectReceipt(object_id, context, ciphertext_digest(encrypted)), encrypted
        )

    @staticmethod
    def names(receipt: ObjectReceipt) -> tuple[str, str]:
        if not isinstance(receipt.object_id, UUID):
            raise UnsafeStorage("An internal UUID object identifier is required.")
        return f"stage-{receipt.object_id.hex}.tmp", f"{receipt.object_id.hex}.evidence"

    @staticmethod
    def write_all(fd: int, content: bytes) -> None:
        remaining = memoryview(content)
        while remaining:
            count = os.write(fd, remaining)
            if count <= 0:
                raise OSError("Incomplete encrypted write")
            remaining = remaining[count:]

    def validate(self, receipt: ObjectReceipt, raw: bytes) -> LabAttestation:
        if not hmac.compare_digest(ciphertext_digest(raw), receipt.envelope_sha256):
            raise EvidenceUnavailable("Evidence unavailable or invalid.")
        return self.codec.open(raw, receipt.object_id, receipt.context)

    def write(self, prepared: PreparedObject) -> ObjectReceipt:
        receipt = prepared.receipt
        temporary, final = self.names(receipt)
        # Even a fabricated PreparedObject cannot persist plaintext through this API.
        self.validate(receipt, prepared.encrypted)
        try:
            with self.directory.lock(exclusive=True):
                if final in os.listdir(self.directory.fd):
                    raise ObjectExists("Evidence object already exists.")
                fd = self.directory.open_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                try:
                    self.write_all(fd, prepared.encrypted)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self.directory.sync()
                # Atomic no-replace publication on the same filesystem. No rename-overwrite.
                os.link(
                    temporary,
                    final,
                    src_dir_fd=self.directory.fd,
                    dst_dir_fd=self.directory.fd,
                    follow_symlinks=False,
                )
                self.directory.sync()
                os.unlink(temporary, dir_fd=self.directory.fd)
                self.directory.sync()
            return receipt
        except FileExistsError:
            raise ObjectExists("Evidence operation already exists; inspect its receipt.") from None
        except OSError:
            raise StorageInterrupted(
                "Encrypted write interrupted; inspect the prepared receipt."
            ) from None

    def read_raw(self, name: str, *, maximum_links: int = 1) -> bytes:
        fd = self.directory.open_file(name, os.O_RDONLY, maximum_links=maximum_links)
        try:
            with os.fdopen(fd, "rb", closefd=False) as stream:
                return stream.read(MAX_ENVELOPE_BYTES + 1)
        finally:
            os.close(fd)

    def read(self, receipt: ObjectReceipt) -> LabAttestation:
        _, final = self.names(receipt)
        try:
            with self.directory.lock(exclusive=False):
                return self.validate(receipt, self.read_raw(final, maximum_links=2))
        except (OSError, UnsafeStorage):
            raise EvidenceUnavailable("Evidence unavailable or invalid.") from None

    def recover(self, receipt: ObjectReceipt) -> str:
        """Validate publication and remove only its proven duplicate temporary link.

        Temporary-only objects are NOT promoted or deleted. Even a valid final
        object is not evidence of a PostgreSQL commit or human review.
        """
        temporary, final = self.names(receipt)
        try:
            with self.directory.lock(exclusive=True):
                entries = os.listdir(self.directory.fd)
                if final not in entries:
                    return "temporary_only" if temporary in entries else "missing"
                self.validate(receipt, self.read_raw(final, maximum_links=2))
                if temporary in entries:
                    first = os.stat(temporary, dir_fd=self.directory.fd, follow_symlinks=False)
                    second = os.stat(final, dir_fd=self.directory.fd, follow_symlinks=False)
                    if (first.st_dev, first.st_ino) != (second.st_dev, second.st_ino):
                        raise UnsafeStorage("Recovery requires an identical temporary hard link.")
                    os.unlink(temporary, dir_fd=self.directory.fd)
                self.directory.sync()
                return "published"
        except OSError:
            raise StorageInterrupted("Local recovery interrupted; preserve the receipt.") from None

    def discard_temporary(self, receipt: ObjectReceipt) -> bool:
        """Explicit abort of this caller's known operation, never age-based garbage collection."""
        temporary, final = self.names(receipt)
        with self.directory.lock(exclusive=True):
            entries = os.listdir(self.directory.fd)
            if final in entries:
                raise ObjectExists("Published objects cannot be discarded by temporary cleanup.")
            if temporary not in entries:
                return False
            fd = self.directory.open_file(temporary, os.O_RDONLY)
            os.close(fd)
            os.unlink(temporary, dir_fd=self.directory.fd)
            self.directory.sync()
            return True
