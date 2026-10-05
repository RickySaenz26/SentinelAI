"""Sanitized storage errors: never include paths, keys or submitted content."""


class EvidenceError(Exception):
    """Base exception for the internal evidence boundary."""


class InvalidEvidence(EvidenceError):
    """Invalid bounded document or envelope."""


class EvidenceUnavailable(EvidenceError):
    """No plaintext may be returned."""


class UnsafeStorage(EvidenceError):
    """Storage cannot provide the required filesystem guarantees."""


class ObjectExists(EvidenceError):
    """Exclusive publication refused to replace an existing object."""


class StorageInterrupted(EvidenceError):
    """Outcome may be uncertain; inspect the receipt before retrying."""
