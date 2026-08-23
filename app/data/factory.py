from app.config import Settings
from app.data.fake import FakeDatabaseGateway
from app.data.gateway import DatabaseGateway
from app.data.postgres import PostgresDatabaseGateway


def build_database_gateway(settings: Settings) -> DatabaseGateway:
    if settings.database_provider == "fake":
        return FakeDatabaseGateway()
    return PostgresDatabaseGateway(settings)
