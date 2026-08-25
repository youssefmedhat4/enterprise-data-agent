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
