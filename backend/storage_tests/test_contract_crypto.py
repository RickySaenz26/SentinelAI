import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap

from app.evidence import filesystem
from app.evidence.configuration import storage_configuration
from app.evidence.contracts import (
    MAX_DOCUMENT_BYTES,
    canonical_json,
    decode_document,
    document_bytes,
    parse_submission,
)
from app.evidence.crypto import Envelope, EnvelopeCodec, decode, encode, header
from app.evidence.errors import EvidenceUnavailable, InvalidEvidence, UnsafeStorage


def test_deterministic_strict_document(body, document):
    other = dict(reversed(list(body.items())))
    other["observed_at"] = "2026-09-30T07:00:00-05:00"
    assert document_bytes(decode_document(json.dumps(other).encode())) == document_bytes(document)
    assert decode_document(document_bytes(document)) == document
    # Historical documents remain readable; freshness is a submission rule only.
    with pytest.raises(InvalidEvidence, match="preceding 24 hours"):
        parse_submission(document_bytes(document), now=datetime(2027, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", 2),
        ("method", "ping"),
        ("lab_asset_reference", "https://example.invalid"),
        ("lab_asset_reference", "../file"),
        ("lab_asset_reference", "curl target"),
        ("lab_asset_reference", "a" * 100),
        ("observed_at", "2026-09-30T12:00:00"),
        ("observations", {"console_identified": True}),
        ("declaration", "approved"),
        ("organization_id", "forged"),
        ("asset_id", "forged"),
        ("ip", "192.0.2.10"),
        ("target", "192.0.2.11"),
        ("password", "synthetic-not-a-secret"),
        ("file", "base64"),
        ("command", "echo"),
        ("url", "https://example.invalid"),
    ],
)
def test_invalid_fields_never_echo_input(body, field, value):
    with pytest.raises(InvalidEvidence, match="Invalid lab control") as result:
        decode_document(canonical_json({**body, field: value}))
    assert "synthetic-not-a-secret" not in str(result.value)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"x",
        b"\xff",
        b"{}" * 9000,
        b"[" * 2000,
        b'{"schema_version":1,"schema_version":1}',
        b'{"x":{"a":1,"a":2}}',
        b"null",
        b"[]",
        b'{"x":NaN}',
    ],
)
def test_malformed_and_oversize_documents(raw):
    with pytest.raises(InvalidEvidence):
        decode_document(raw)


def test_document_limit_and_freshness_boundaries(body, document):
    raw = canonical_json(body)
    assert decode_document(raw + b" " * (MAX_DOCUMENT_BYTES - len(raw))) == document
    with pytest.raises(InvalidEvidence):
        decode_document(raw + b" " * (MAX_DOCUMENT_BYTES + 1 - len(raw)))
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert parse_submission(raw, now=now) == document
    for invalid in (
        now + timedelta(microseconds=1),
        document.observed_at - timedelta(seconds=1),
        now.replace(tzinfo=None),
    ):
        with pytest.raises(InvalidEvidence):
            parse_submission(raw, now=invalid)


def test_roundtrip_randomness_and_header_binding(codec, context, document, keys):
    identifier = uuid4()
    first = codec.seal(identifier, context, document)
    second = codec.seal(identifier, context, document)
    assert first != second
    assert codec.open(first, identifier, context) == document
    assert document.lab_asset_reference.encode() not in first
    assert document_bytes(document) not in first
    one, two = json.loads(first), json.loads(second)
    assert len(decode(one["nonce"])) == 12
    assert len(decode(one["wrapped_key"])) == 40
    assert one["wrapped_key"] != two["wrapped_key"]
    keys.values["alias"] = keys.values["lab-1"]
    one["key_id"] = "alias"  # key-id metadata itself is authenticated
    with pytest.raises(EvidenceUnavailable):
        codec.open(canonical_json(one), identifier, context)


@pytest.mark.parametrize("field", ["organization_id", "asset_id", "dossier_id", "version"])
def test_cross_context_denied(codec, context, document, field):
    identifier = uuid4()
    raw = codec.seal(identifier, context, document)
    changed = context.model_copy(update={field: 2 if field == "version" else uuid4()})
    with pytest.raises(EvidenceUnavailable):
        codec.open(raw, identifier, changed)
    with pytest.raises(EvidenceUnavailable):
        codec.open(raw, uuid4(), context)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", 2),
        ("format_version", True),
        ("algorithm", "none"),
        ("nonce", "!"),
        ("nonce", encode(b"n")),
        ("wrapped_key", encode(b"k")),
        ("wrapped_key", encode(os.urandom(40))),
        ("ciphertext", encode(b"short")),
        ("ciphertext", encode(os.urandom(64))),
        ("key_id", "unknown"),
        ("key_id", "../secret"),
        ("extra", "no"),
        ("context", {}),
    ],
)
def test_envelope_tampering(codec, context, document, field, value):
    identifier = uuid4()
    envelope = json.loads(codec.seal(identifier, context, document))
    envelope[field] = value
    with pytest.raises(EvidenceUnavailable):
        codec.open(canonical_json(envelope), identifier, context)


@pytest.mark.parametrize("raw", [b"", b"{}", b"\xff", b"x" * 25000, b"null", b'{"a":1,"a":2}'])
def test_invalid_envelope_bytes(codec, context, raw):
    with pytest.raises(EvidenceUnavailable):
        codec.open(raw, uuid4(), context)


def test_truncation_wrong_missing_and_invalid_key(codec, keys, context, document):
    identifier = uuid4()
    raw = codec.seal(identifier, context, document)
    with pytest.raises(EvidenceUnavailable):
        codec.open(raw[:-1], identifier, context)
    for material in (os.urandom(32), b"short", "not-bytes"):
        keys.values["lab-1"] = material
        with pytest.raises(EvidenceUnavailable):
            codec.open(raw, identifier, context)
    keys.values.clear()
    with pytest.raises(EvidenceUnavailable):
        codec.open(raw, identifier, context)
    with pytest.raises(EvidenceUnavailable):
        codec.seal(identifier, context, document)


def test_key_rotation_selection_keeps_old_reader(keys, context, document):
    old, new = EnvelopeCodec(keys, "lab-1"), EnvelopeCodec(keys, "lab-2")
    identifier = uuid4()
    raw = old.seal(identifier, context, document)
    assert new.open(raw, identifier, context) == document
    assert json.loads(new.seal(uuid4(), context, document))["key_id"] == "lab-2"


def test_noncanonical_plaintext_and_base64_rejected(codec, keys, context, document):
    identifier = uuid4()
    envelope = Envelope.model_validate_json(codec.seal(identifier, context, document))
    dek = aes_key_unwrap(keys.get_key("lab-1"), decode(envelope.wrapped_key))
    ciphertext = AESGCM(dek).encrypt(
        decode(envelope.nonce), b" " + document_bytes(document), header(envelope)
    )
    changed = envelope.model_copy(update={"ciphertext": encode(ciphertext)})
    with pytest.raises(EvidenceUnavailable):
        codec.open(canonical_json(changed.model_dump(mode="json")), identifier, context)
    with pytest.raises(ValueError, match="Noncanonical"):
        decode("Zh==")
    with pytest.raises(InvalidEvidence):
        document_bytes(document.model_copy(update={"schema_version": 9}))


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"LAB_EVIDENCE_ROOT": "/tmp/evidence"},
        {
            "LAB_EVIDENCE_ROOT": "/tmp/evidence",
            "LAB_EVIDENCE_KEY_ROOT": "/tmp/keys",
            "LAB_EVIDENCE_ACTIVE_KEY_ID": "../bad",
        },
    ],
)
def test_configuration_absent_invalid_denies(environment):
    with pytest.raises(UnsafeStorage, match="not configured"):
        storage_configuration(environment)


def test_separate_configuration_and_native_windows_fail_closed(sandbox, monkeypatch):
    environment = {
        "LAB_EVIDENCE_ROOT": str(sandbox / "storage"),
        "LAB_EVIDENCE_KEY_ROOT": str(sandbox / "keys"),
        "LAB_EVIDENCE_ACTIVE_KEY_ID": "lab-1",
    }
    assert storage_configuration(environment)[2] == "lab-1"
    for bad in (
        "relative",
        str(sandbox / "storage"),
        str(sandbox / "storage" / "sub"),
        str(sandbox / "storage" / ".." / "keys"),
    ):
        with pytest.raises(UnsafeStorage):
            storage_configuration({**environment, "LAB_EVIDENCE_KEY_ROOT": bad})
    monkeypatch.setattr(filesystem.sys, "platform", "win32")
    with pytest.raises(UnsafeStorage, match="requires Linux"):
        filesystem.PrivateDirectory(sandbox / "storage", repository_root=sandbox / "repository")
