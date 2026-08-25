from app.config import Settings
from app.data.factory import build_database_gateway
from app.data.fake import FakeDatabaseGateway
from app.data.postgres import PostgresDatabaseGateway
from app.data.toolbox import ToolboxDatabaseGateway


def test_database_factory_defaults_to_fake() -> None:
    gateway = build_database_gateway(Settings())

    assert isinstance(gateway, FakeDatabaseGateway)


def test_database_factory_can_select_postgres() -> None:
    gateway = build_database_gateway(Settings(DATABASE_PROVIDER="postgres"))

    assert isinstance(gateway, PostgresDatabaseGateway)


def test_database_factory_can_select_toolbox_without_fallback() -> None:
    gateway = build_database_gateway(Settings(DATABASE_PROVIDER="toolbox"))

    assert isinstance(gateway, ToolboxDatabaseGateway)
