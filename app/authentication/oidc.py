from __future__ import annotations

import asyncio
from collections.abc import Mapping
from time import monotonic
from typing import Any

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.authentication.gateway import (
    AuthenticationCredentials,
    AuthenticationFailedError,
    AuthenticationGateway,
    AuthenticationProviderUnavailableError,
    UserIdentity,
)


class _OIDCMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    issuer: str
    jwks_uri: str


class _JWKSet(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    keys: tuple[dict[str, Any], ...] = Field(min_length=1)


class OIDCAuthenticationGateway(AuthenticationGateway):
    """Validate bearer JWTs against one configured OIDC issuer and its JWKS."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        discovery_url: str | None,
        subject_claim: str,
        roles_claim: str,
        display_name_claim: str,
        attribute_claims: tuple[str, ...],
        cache_seconds: int,
        clock_skew_seconds: int,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._discovery_url = discovery_url or (
            self._issuer + "/.well-known/openid-configuration"
        )
        self._subject_claim = subject_claim
        self._roles_claim = roles_claim
        self._display_name_claim = display_name_claim
        self._attribute_claims = attribute_claims
        self._cache_seconds = cache_seconds
        self._clock_skew_seconds = clock_skew_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._metadata: _OIDCMetadata | None = None
        self._jwks: _JWKSet | None = None
        self._cache_loaded_at = 0.0
        self._cache_lock = asyncio.Lock()

    async def authenticate(self, credentials: AuthenticationCredentials) -> UserIdentity:
        if credentials.bearer_token is None:
            raise AuthenticationFailedError("A bearer access token is required.")
        token = credentials.bearer_token.get_secret_value()
        metadata, jwks = await self._provider_configuration()
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            key_id = header.get("kid")
            if algorithm != "RS256" or not isinstance(key_id, str):
                raise AuthenticationFailedError("The bearer token header is not accepted.")
            key_data = next((item for item in jwks.keys if item.get("kid") == key_id), None)
            if key_data is None:
                metadata, jwks = await self._provider_configuration(force_refresh=True)
                key_data = next(
                    (item for item in jwks.keys if item.get("kid") == key_id),
                    None,
                )
            if key_data is None:
                raise AuthenticationFailedError("The bearer token signing key is unknown.")
            signing_key = jwt.PyJWK.from_dict(key_data, algorithm="RS256")
            claims = jwt.decode(
                token,
                key=signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=metadata.issuer,
                leeway=self._clock_skew_seconds,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except AuthenticationFailedError:
            raise
        except jwt.PyJWTError as exc:
            raise AuthenticationFailedError("The bearer access token is invalid.") from exc
        subject = claims.get(self._subject_claim)
        if not isinstance(subject, str) or not subject:
            raise AuthenticationFailedError("The bearer token has no valid subject claim.")
        return UserIdentity(
            subject_id=subject,
            roles=_string_tuple(claims.get(self._roles_claim)),
            display_name=_optional_string(claims.get(self._display_name_claim)),
            attributes={
                claim: value
                for claim in self._attribute_claims
                if (value := _attribute_value(claims.get(claim))) is not None
            },
            provider="oidc",
        )

    async def _provider_configuration(
        self,
        *,
        force_refresh: bool = False,
    ) -> tuple[_OIDCMetadata, _JWKSet]:
        if not force_refresh and self._cache_is_valid():
            assert self._metadata is not None and self._jwks is not None
            return self._metadata, self._jwks
        async with self._cache_lock:
            if not force_refresh and self._cache_is_valid():
                assert self._metadata is not None and self._jwks is not None
                return self._metadata, self._jwks
            try:
                metadata_response = await self._client.get(self._discovery_url)
                metadata_response.raise_for_status()
                metadata = _OIDCMetadata.model_validate(metadata_response.json())
                if metadata.issuer.rstrip("/") != self._issuer:
                    raise AuthenticationProviderUnavailableError(
                        "OIDC discovery returned an unexpected issuer."
                    )
                jwks_response = await self._client.get(metadata.jwks_uri)
                jwks_response.raise_for_status()
                jwks = _JWKSet.model_validate(jwks_response.json())
            except AuthenticationProviderUnavailableError:
                raise
            except (httpx.HTTPError, ValueError, ValidationError) as exc:
                raise AuthenticationProviderUnavailableError(
                    "The configured OIDC provider is unavailable."
                ) from exc
            self._metadata = metadata
            self._jwks = jwks
            self._cache_loaded_at = monotonic()
            return metadata, jwks

    def _cache_is_valid(self) -> bool:
        return (
            self._metadata is not None
            and self._jwks is not None
            and monotonic() - self._cache_loaded_at < self._cache_seconds
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(item for item in value if isinstance(item, str) and item)
    return ()


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _attribute_value(value: object) -> str | None:
    if isinstance(value, str | int | float | bool):
        return str(value)
    if isinstance(value, list | tuple) and all(isinstance(item, str) for item in value):
        return ",".join(value)
    if isinstance(value, Mapping):
        return None
    return None
