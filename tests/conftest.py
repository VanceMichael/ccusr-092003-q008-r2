"""pytest 公共夹具：临时数据库与内存间 HTTP 服务。"""

import http.client
import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from app import db
from app.main import Handler


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    connection = db.connect(db_path)
    db.migrate(connection)
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture()
def base_url(tmp_path, monkeypatch):
    db_path = tmp_path / "http.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


class Client:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def request(self, method: str, path: str, payload=None):
        host, port = self.base_url.removeprefix("http://").split(":")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection = http.client.HTTPConnection(host, int(port), timeout=5)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return response.status, parsed


@pytest.fixture()
def client(base_url):
    return Client(base_url)
