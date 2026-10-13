"""M6-04 主路径共用的已迁移 TestClient。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def migrated(pg_available: bool) -> None:
    if not pg_available:
        pytest.skip("需要 PostgreSQL")
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database_migration_url.replace("%", "%%"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="module")
def client(migrated: None) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
