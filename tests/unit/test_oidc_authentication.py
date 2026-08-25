from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.authentication.gateway import (
    AuthenticationCredentials,
    AuthenticationFailedError,
    AuthenticationProviderUnavailableError,
)
from app.authentication.oidc import OIDCAuthenticationGateway

ISSUER = "https://identity.example.test/tenant/v2.0"
AUDIENCE = "api://enterprise-data-agent"
DISCOVERY = "https://identity.example.test/openid-configuration"
JWKS = "https://identity.example.test/keys"


def _key_pair() -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    return private_key, public_jwk


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_delta: timedelta = timedelta(minutes=5),
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "sub": "subject-123",
            "iat": now,
            "nbf": now - timedelta(seconds=1),
            "exp": now + expires_delta,
            "roles": ["analyst", "hr_analyst"],
            "name": "Test Analyst",
            "tid": "tenant-123",
            "preferred_username": "analyst@example.test",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def _gateway(public_jwk: dict[str, Any]) -> OIDCAuthenticationGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DISCOVERY:
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": JWKS})
        if str(request.url) == JWKS:
            return httpx.Response(200, json={"keys": [public_jwk]})
        return httpx.Response(404)

    return OIDCAuthenticationGateway(
        issuer=ISSUER,
        audience=AUDIENCE,
        discovery_url=DISCOVERY,
        subject_claim="sub",
        roles_claim="roles",
        display_name_claim="name",
        attribute_claims=("tid", "preferred_username"),
        cache_seconds=3600,
        clock_skew_seconds=0,
        timeout_seconds=1,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_oidc_validates_signed_token_and_maps_identity_claims() -> None:
    private_key, public_jwk = _key_pair()
    gateway = _gateway(public_jwk)

    identity = await gateway.authenticate(
        AuthenticationCredentials(bearer_token=_token(private_key))
    )

    assert identity.subject_id == "subject-123"
    assert identity.roles == ("analyst", "hr_analyst")
    assert identity.display_name == "Test Analyst"
    assert identity.attributes == {
        "tid": "tenant-123",
        "preferred_username": "analyst@example.test",
    }
    assert identity.provider == "oidc"
    await gateway.close()


@pytest.mark.parametrize(
    ("claims", "expires_delta"),
    [
        ({"audience": "api://wrong"}, timedelta(minutes=5)),
        ({"issuer": "https://wrong.example.test"}, timedelta(minutes=5)),
        ({}, timedelta(minutes=-5)),
    ],
)
@pytest.mark.asyncio
async def test_oidc_rejects_wrong_audience_issuer_and_expired_tokens(
    claims: dict[str, str],
    expires_delta: timedelta,
) -> None:
    private_key, public_jwk = _key_pair()
    gateway = _gateway(public_jwk)
    token = _token(private_key, expires_delta=expires_delta, **claims)

    with pytest.raises(AuthenticationFailedError):
        await gateway.authenticate(AuthenticationCredentials(bearer_token=token))
    await gateway.close()


@pytest.mark.asyncio
async def test_oidc_rejects_invalid_signature() -> None:
    trusted_key, public_jwk = _key_pair()
    untrusted_key, _ = _key_pair()
    del trusted_key
    gateway = _gateway(public_jwk)

    with pytest.raises(AuthenticationFailedError):
        await gateway.authenticate(
            AuthenticationCredentials(bearer_token=_token(untrusted_key))
        )
    await gateway.close()


@pytest.mark.asyncio
async def test_oidc_provider_unavailable_is_typed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("identity provider unavailable", request=request)

    gateway = OIDCAuthenticationGateway(
        issuer=ISSUER,
        audience=AUDIENCE,
        discovery_url=DISCOVERY,
        subject_claim="sub",
        roles_claim="roles",
        display_name_claim="name",
        attribute_claims=(),
        cache_seconds=3600,
        clock_skew_seconds=0,
        timeout_seconds=1,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(AuthenticationProviderUnavailableError):
        await gateway.authenticate(
            AuthenticationCredentials(bearer_token="header.payload.signature")
        )
    await gateway.close()
