"""Storage primitives require no DB. All keys and directories are disposable fixtures."""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.evidence.contracts import EvidenceContext, canonical_json, parse_submission
from app.evidence.crypto import EnvelopeCodec
from app.evidence.errors import EvidenceUnavailable


class FixtureKeys:
    def __init__(self):
        self.values = {"lab-1": os.urandom(32), "lab-2": os.urandom(32)}

    def get_key(self, key_id):
        try:
            return self.values[key_id]
        except KeyError:
            raise EvidenceUnavailable("Evidence key unavailable.") from None


@pytest.fixture
def keys():
    return FixtureKeys()


@pytest.fixture
def codec(keys):
    return EnvelopeCodec(keys, "lab-1")


@pytest.fixture
def context():
    return EvidenceContext(organization_id=uuid4(), asset_id=uuid4(), dossier_id=uuid4(), version=1)


@pytest.fixture
def body():
    return {
        "schema_version": 1,
        "method": "supervised_local_console",
        "observed_at": "2026-09-30T12:00:00Z",
        "lab_asset_reference": "LAB-123456",
        "observations": {
            "console_identified": "observed",
            "inventory_ipv4_matches": "observed",
            "administrative_control": "observed",
        },
        "declaration": "technical_control_only_not_ownership_or_scan_permission",
    }


@pytest.fixture
def document(body):
    return parse_submission(canonical_json(body), now=datetime(2026, 9, 30, 12, tzinfo=UTC))


@pytest.fixture
def sandbox():
    with tempfile.TemporaryDirectory(prefix="sentinelai-evidence-test-") as directory:
        root = Path(directory)
        for name in ("storage", "keys", "repository"):
            (root / name).mkdir(mode=0o700)
        yield root
    assert not root.exists(), "Owned ephemeral storage/key fixture was not removed"
