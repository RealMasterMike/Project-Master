from __future__ import annotations

import os
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_master.integrations.comfyui.security import (
    ComfySecurityError,
    is_loopback_host,
    normalize_base_url,
    normalize_host,
    validate_auth_header,
)


class SecretRef(BaseModel):
    """A persisted reference to a secret; secret material is never part of this model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["environment", "keyring"] = "environment"
    key: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")


class SecretResolver(Protocol):
    def resolve(self, reference: SecretRef) -> str: ...


class EnvironmentSecretResolver:
    """Resolve environment references at the final transport boundary."""

    def resolve(self, reference: SecretRef) -> str:
        if reference.source != "environment":
            raise LookupError(f"Secret source {reference.source!r} is not available.")
        value = os.getenv(reference.key)
        if value is None:
            raise LookupError(f"Secret reference {reference.key!r} is not configured.")
        if not value:
            raise LookupError(f"Secret reference {reference.key!r} resolved to an empty value.")
        return value


class ComfyAuth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: Literal["bearer", "header"] = "bearer"
    secret_ref: SecretRef
    header_name: str = "Authorization"

    @model_validator(mode="after")
    def validate_header(self) -> ComfyAuth:
        validate_auth_header(self.header_name)
        if self.scheme == "bearer" and self.header_name.lower() != "authorization":
            raise ValueError("Bearer authentication must use the Authorization header.")
        return self


class ComfyUIProfile(BaseModel):
    """Connection settings safe to persist in Project Master state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=120)
    base_url: str = "http://127.0.0.1:8188"
    trusted_hosts: tuple[str, ...] = ()
    verify_tls: bool = True
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    auth: ComfyAuth | None = None

    @field_validator("trusted_hosts")
    @classmethod
    def normalize_trusted_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(normalize_host(item) for item in value))

    @model_validator(mode="after")
    def validate_endpoint(self) -> ComfyUIProfile:
        normalized = normalize_base_url(self.base_url, self.trusted_hosts)
        host = url_host(normalized)
        if not is_loopback_host(host) and not self.verify_tls:
            raise ValueError("TLS verification cannot be disabled for a remote ComfyUI host.")
        object.__setattr__(self, "base_url", normalized)
        return self

    def authentication_headers(self, resolver: SecretResolver) -> dict[str, str]:
        if self.auth is None:
            return {}
        secret = resolver.resolve(self.auth.secret_ref)
        if "\r" in secret or "\n" in secret:
            raise ValueError("Resolved ComfyUI secrets cannot contain header line breaks.")
        value = f"Bearer {secret}" if self.auth.scheme == "bearer" else secret
        return {self.auth.header_name: value}


def url_host(value: str) -> str:
    from urllib.parse import urlsplit

    host = urlsplit(value).hostname
    if host is None:
        raise ComfySecurityError("ComfyUI URL has no host.")
    return host
