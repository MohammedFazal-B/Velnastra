"""UI transport and read-only audit data contracts; no model or verdict changes."""
from contextlib import closing
from http.server import ThreadingHTTPServer
import json
import sqlite3
import threading
from unittest.mock import MagicMock
from urllib.request import urlopen, Request
from urllib.error import HTTPError

import pytest
from src import analytics
from src import server as web


@pytest.fixture
def interface(monkeypatch):
    rows = [{"id":7,"timestamp":"2026-11-19T10:00:00+00:00","prompt":"<script>untrusted input</script>",
             "status":"BLOCK","category":"prompt_injection","reason":"intent","score":0.9,"latency_ms":1500}]
    monkeypatch.setattr(web,"get_recent_requests",lambda:rows)
    service=ThreadingHTTPServer(("127.0.0.1",0),web.make_handler(MagicMock(),MagicMock(),object()))
    worker=threading.Thread(target=service.serve_forever,daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{service.server_port}"
    finally:
        service.shutdown();service.server_close();worker.join()


def test_interface_bundle_is_served_locally(interface):
    for path,kind in [("/","text/html"),("/assets/styles.css","text/css"),("/assets/app.js","text/javascript")]:
        with urlopen(interface+path,timeout=5) as response:
            assert response.status==200
            assert response.headers["Content-Type"].startswith(kind)
            assert response.headers["Cache-Control"]=="no-store"
            assert response.headers["X-Content-Type-Options"]=="nosniff"
            body=response.read().decode()
            assert body
            if path=="/":
                assert "/assets/app.js" in body and "/assets/styles.css" in body
                assert "fonts.googleapis" not in body
                assert "Assessment result" in body


@pytest.mark.parametrize("path",["/assets/../.env","/assets/%2e%2e/.env","/assets/config.py","/src/config.py"])
def test_asset_routes_do_not_expose_project_files(interface,path):
    with pytest.raises(HTTPError) as error:
        urlopen(interface+path,timeout=5)
    assert error.value.code==404


def test_activity_api_returns_audit_data_as_json(interface):
    with urlopen(interface+"/api/activity",timeout=5) as response:
        payload=json.load(response)
    assert payload["limit"]==30
    assert payload["requests"][0]["id"]==7
    assert payload["requests"][0]["prompt"]=="<script>untrusted input</script>"


def test_audit_ids_and_recent_order_match_persisted_records(monkeypatch,tmp_path):
    monkeypatch.setattr(analytics,"DB_PATH",tmp_path/"audit.db")
    analytics.init_db()
    first=analytics.log_request("first","ALLOW","safe",0.1,20,"benign")
    second=analytics.log_request("second","BLOCK","intent",0.2,30,"code_execution")
    recent=analytics.get_recent_requests(1)
    assert first < second
    assert len(recent)==1 and recent[0]["id"]==second and recent[0]["prompt"]=="second"
    with closing(sqlite3.connect(analytics.DB_PATH)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]==2


@pytest.mark.parametrize("limit",[0,101,True,"30"])
def test_activity_limits_are_validated_before_query(limit):
    with pytest.raises(ValueError):
        analytics.get_recent_requests(limit)
