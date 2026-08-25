from app.authentication.gateway import AuthenticationGateway
from app.authentication.local import LocalDevelopmentAuthenticationGateway
from app.authentication.oidc import OIDCAuthenticationGateway
from app.config import Settings


def build_authentication_gateway(settings: Settings) -> AuthenticationGateway:
    if settings.authentication_provider == "local":
        return LocalDevelopmentAuthenticationGateway(
            subject_id=settings.local_auth_subject,
            roles=settings.local_auth_roles,
        )
    assert settings.oidc_issuer is not None and settings.oidc_audience is not None
    return OIDCAuthenticationGateway(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        discovery_url=settings.oidc_discovery_url,
        subject_claim=settings.oidc_subject_claim,
        roles_claim=settings.oidc_roles_claim,
        display_name_claim=settings.oidc_display_name_claim,
        attribute_claims=settings.oidc_attribute_claims,
        cache_seconds=settings.oidc_cache_seconds,
        clock_skew_seconds=settings.oidc_clock_skew_seconds,
        timeout_seconds=settings.oidc_timeout_seconds,
    )
