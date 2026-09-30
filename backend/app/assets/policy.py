"""Pure, fail-closed laboratory policy. Never resolves or contacts a target."""

import hashlib
from ipaddress import IPv4Address, IPv4Network
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

BLOCKED_NETWORKS = tuple(
    IPv4Network(network)
    for network in ("0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4")
)


def exact_ipv4(value: str) -> str:
    # Python 3.12 rejects whitespace, non-decimal notation and leading zero octets.
    return str(IPv4Address(value))


class LabPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    allowed_targets: tuple[str, ...] = Field(max_length=10000)
    excluded_targets: tuple[str, ...] = Field(max_length=10000)
    max_active_assets_per_tenant: int = Field(ge=1, le=10000)

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Policy version must be an integer.")
        return value

    @field_validator("allowed_targets", "excluded_targets")
    @classmethod
    def validate_targets(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            exact_ipv4(value)
        if len(values) != len(set(values)):
            raise ValueError("Policy targets must be unique.")
        return values

    def permits(self, target: str) -> bool:
        address = IPv4Address(exact_ipv4(target))
        return (
            target in self.allowed_targets
            and target not in self.excluded_targets
            and not any(address in network for network in BLOCKED_NETWORKS)
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
