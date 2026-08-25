from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class AuthenticationCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bearer_token: SecretStr | None = Field(default=None, exclude=True)


class UserIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str = Field(min_length=1)
    roles: tuple[str, ...] = ()
    display_name: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    provider: str = Field(min_length=1)


class AuthenticationGatewayError(RuntimeError):
    """Base error for authentication adapters."""


class AuthenticationFailedError(AuthenticationGatewayError):
    """Raised when supplied credentials cannot establish an identity."""


class AuthenticationProviderUnavailableError(AuthenticationGatewayError):
    """Raised when an external identity provider cannot be reached."""


class AuthenticationGateway(Protocol):
    async def authenticate(self, credentials: AuthenticationCredentials) -> UserIdentity:
        """Authenticate credentials and return a provider-independent identity."""

    async def close(self) -> None:
        """Release authentication-provider resources."""
