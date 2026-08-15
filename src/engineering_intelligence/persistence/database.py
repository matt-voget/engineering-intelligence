"""SQLite engine and migration management."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def create_sqlite_engine(database_path: Path) -> Engine:
    """Create a SQLite engine with foreign keys and WAL durability enabled."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database_path}", future=True)

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def upgrade_database(database_path: Path) -> None:
    """Upgrade a database to the latest checked-in schema."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    repository_root = Path(__file__).resolve().parents[3]
    config = Config(repository_root / "alembic.ini")
    config.set_main_option("script_location", str(repository_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
