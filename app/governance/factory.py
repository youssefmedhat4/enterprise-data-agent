from app.config import Settings
from app.governance.disabled import DisabledGovernanceGateway
from app.governance.gateway import GovernanceGateway
from app.governance.openmetadata import OpenMetadataGovernanceGateway


def build_governance_gateway(settings: Settings) -> GovernanceGateway:
    if settings.governance_provider == "disabled":
        return DisabledGovernanceGateway()
    if settings.governance_provider == "openmetadata":
        token = (
            settings.openmetadata_jwt_token.get_secret_value()
            if settings.openmetadata_jwt_token is not None
            else None
        )
        return OpenMetadataGovernanceGateway(
            api_url=settings.openmetadata_api_url,
            fqn_prefix=settings.openmetadata_fqn_prefix,
            timeout_seconds=settings.openmetadata_timeout_seconds,
            jwt_token=token,
            include_lineage=settings.openmetadata_include_lineage,
            sensitivity_classifications=settings.openmetadata_sensitivity_classifications,
        )
    raise ValueError(f"Unsupported governance provider: {settings.governance_provider}")
