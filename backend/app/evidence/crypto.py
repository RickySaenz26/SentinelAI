"""Versioned AEAD envelope. No filesystem, network, SQL or authorization."""

import base64
import binascii
import hashlib
import os
from typing import Literal, Protocol
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap, aes_key_wrap
from pydantic import Field

from app.evidence.contracts import (
    ClosedModel,
    EvidenceContext,
    LabAttestation,
    bounded_json,
    canonical_json,
    decode_document,
    document_bytes,
)
from app.evidence.errors import EvidenceUnavailable, InvalidEvidence

MAX_ENVELOPE_BYTES = 24 * 1024
KEY_ID_PATTERN = r"^[a-z][a-z0-9_-]{0,47}$"


class KeyProvider(Protocol):
    def get_key(self, key_id: str) -> bytes: ...


class Envelope(ClosedModel):
    format_version: int = Field(ge=1, le=1)
    algorithm: Literal["AES-256-GCM+A256KW"]
    object_id: UUID
    context: EvidenceContext
    key_id: str = Field(pattern=KEY_ID_PATTERN)
    nonce: str
    wrapped_key: str
    ciphertext: str


def encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def decode(value: str) -> bytes:
    raw = base64.b64decode(value, validate=True)
    if encode(raw) != value:
        raise ValueError("Noncanonical base64")
    return raw


def header(envelope: Envelope) -> bytes:
    return canonical_json(envelope.model_dump(mode="json", exclude={"ciphertext"}))


def key(provider: KeyProvider, key_id: str) -> bytes:
    value = provider.get_key(key_id)
    if not isinstance(value, bytes) or len(value) != 32:
        raise EvidenceUnavailable("Evidence key unavailable.")
    return value


class EnvelopeCodec:
    def __init__(self, provider: KeyProvider, active_key_id: str):
        self.provider = provider
        self.active_key_id = active_key_id

    def seal(self, object_id: UUID, context: EvidenceContext, document: LabAttestation) -> bytes:
        plaintext = document_bytes(document)
        data_key = AESGCM.generate_key(bit_length=256)
        envelope = Envelope(
            format_version=1,
            algorithm="AES-256-GCM+A256KW",
            object_id=object_id,
            context=context,
            key_id=self.active_key_id,
            nonce=encode(os.urandom(12)),
            wrapped_key=encode(aes_key_wrap(key(self.provider, self.active_key_id), data_key)),
            ciphertext="",
        )
        ciphertext = AESGCM(data_key).encrypt(decode(envelope.nonce), plaintext, header(envelope))
        return canonical_json(
            envelope.model_copy(update={"ciphertext": encode(ciphertext)}).model_dump(mode="json")
        )

    def open(self, raw: bytes, object_id: UUID, context: EvidenceContext) -> LabAttestation:
        try:
            value = bounded_json(raw, MAX_ENVELOPE_BYTES)
            envelope = Envelope.model_validate_json(canonical_json(value))
            if envelope.object_id != object_id or envelope.context != context:
                raise ValueError("Context mismatch")
            nonce, wrapped, ciphertext = (
                decode(envelope.nonce),
                decode(envelope.wrapped_key),
                decode(envelope.ciphertext),
            )
            if len(nonce) != 12 or len(wrapped) != 40 or not 16 <= len(ciphertext) <= 16400:
                raise ValueError("Invalid envelope lengths")
            data_key = aes_key_unwrap(key(self.provider, envelope.key_id), wrapped)
            plaintext = AESGCM(data_key).decrypt(nonce, ciphertext, header(envelope))
            document = decode_document(plaintext)
            if document_bytes(document) != plaintext:
                raise ValueError("Noncanonical document")
            return document
        except (ValueError, InvalidTag, InvalidUnwrap, InvalidEvidence, binascii.Error):
            raise EvidenceUnavailable("Evidence unavailable or invalid.") from None


def ciphertext_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
