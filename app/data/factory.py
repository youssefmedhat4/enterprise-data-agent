from pydantic import PostgresDsn

from app.config import Settings
from app.data.fake import FakeDatabaseGateway
from app.data.gateway import DatabaseGateway
from app.data.postgres import PostgresDatabaseGateway
from app.data.toolbox import ToolboxDatabaseGateway


def build_database_gateway(settings: Settings) -> DatabaseGateway:
    if settings.database_provider == "fake":
        return FakeDatabaseGateway()
    if settings.database_provider == "postgres":
        return PostgresDatabaseGateway(settings)
    if settings.database_provider == "toolbox":
        return ToolboxDatabaseGateway(settings)
    raise ValueError(f"Unsupported database provider: {settings.database_provider}")


def build_database_gateway_for(
    settings: Settings,
    *,
    database_url: str,
    allowed_schemas: tuple[str, ...],
) -> DatabaseGateway:
    """A gateway pointed at one registered datasource.

    Every other limit -- timeouts, row and byte caps, the read-only requirement
    -- still comes from process settings, so a second datasource cannot be
    onboarded with weaker safety than the first. Only the connection and the
    schema scope differ, because those are properties of the database rather
    than of this deployment.
    """
    # `model_copy` deliberately skips validation, so the URL is coerced to the
    # same type the settings field declares rather than left as a plain string.
    scoped = settings.model_copy(
        update={
            "database_url": PostgresDsn(database_url),
            "database_allowed_schemas_csv": ",".join(allowed_schemas),
        }
    )
    return build_database_gateway(scoped)
