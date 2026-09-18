"""pytest 公共配置：隔离的临时 SQLite 与数据目录。"""
import pathlib
import tempfile

import pytest

from app.core import config

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="ai_speaking_test_"))
config.settings.provider_mode = "mock"
config.settings.data_dir = _TMP / "data"
config.settings.database_url = f"sqlite:///{_TMP / 'test.db'}"

from app.main import app  # noqa: E402
from app.models.database import Base, engine  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
