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
    sample_columns: tuple[str, ...] = (),
    database_type: str = "postgres",
) -> DatabaseGateway:
    """A gateway pointed at one registered datasource.

    Every other limit -- timeouts, row and byte caps, the read-only requirement
    -- still comes from process settings, so a second datasource cannot be
    onboarded with weaker safety than the first. Only the connection and the
    schema scope differ, because those are properties of the database rather
    than of this deployment.

    The *provider* comes from the datasource, not from the process. A
    registered datasource names a real database; inheriting a process default
    of `fake` would have answered a question about that database from fixtures
    while reporting the datasource as its source, which is the same class of
    error as executing against the wrong database entirely.
    """
    if database_type != "postgres":
        raise ValueError(f"Unsupported datasource database type: {database_type!r}")
    # `model_copy` deliberately skips validation, so the URL is coerced to the
    # same type the settings field declares rather than left as a plain string.
    scoped = settings.model_copy(
        update={
            "database_provider": database_type,
            "database_url": PostgresDsn(database_url),
            "database_allowed_schemas_csv": ",".join(allowed_schemas),
            "database_sample_columns_csv": ",".join(sample_columns),
        }
    )
    return build_database_gateway(scoped)
