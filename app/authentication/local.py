from app.authentication.gateway import (
    AuthenticationCredentials,
    AuthenticationGateway,
    UserIdentity,
)


class LocalDevelopmentAuthenticationGateway(AuthenticationGateway):
    """Return one explicitly configured development identity without implementing login."""

    def __init__(self, *, subject_id: str, roles: tuple[str, ...]) -> None:
        self._identity = UserIdentity(
            subject_id=subject_id,
            roles=roles,
            display_name="Local development user",
            provider="local",
        )

    async def authenticate(self, credentials: AuthenticationCredentials) -> UserIdentity:
        del credentials
        return self._identity

    async def close(self) -> None:
        return None


def default_development_identity() -> UserIdentity:
    return UserIdentity(
        subject_id="local-developer",
        roles=("admin_analytics",),
        display_name="Local development user",
        provider="local",
    )
