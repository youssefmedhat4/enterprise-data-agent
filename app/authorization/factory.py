from pathlib import Path

from app.authorization.gateway import AuthorizationGateway
from app.authorization.local import LocalPolicyAuthorizationGateway
from app.authorization.opa import OPAAuthorizationGateway
from app.config import Settings


def build_authorization_gateway(settings: Settings) -> AuthorizationGateway:
    if settings.authorization_provider == "local":
        return LocalPolicyAuthorizationGateway(Path(settings.local_authorization_policy_path))
    if settings.authorization_provider == "opa":
        return OPAAuthorizationGateway(
            base_url=settings.opa_url,
            decision_path=settings.opa_decision_path,
            timeout_seconds=settings.opa_timeout_seconds,
        )
    raise ValueError(f"Unsupported authorization provider: {settings.authorization_provider}")
