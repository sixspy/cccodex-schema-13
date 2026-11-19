import pytest

from app.db import init_db
from app.main import create_app


@pytest.fixture
async def client(aiohttp_client, tmp_path):
    db = tmp_path / "test.db"
    app = create_app(str(db))
    return await aiohttp_client(app)


@pytest.fixture
async def authed(client):
    r = await client.post("/api/login", json={"username": "admin", "password": "123456"})
    assert r.status == 200
    return client


@pytest.fixture
def client_db(tmp_path):
    """服务层直插的独立库，与 HTTP fixture 的库分开，避免互相污染。"""
    path = str(tmp_path / "service.db")
    init_db(path)
    return path
