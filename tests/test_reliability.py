"""Failure-path tests for serving, storage, startup, and incremental refresh."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import http.client
import json
import os
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pandas as pd
import pytest
from http.server import ThreadingHTTPServer
from src import server as server_module
from src import database


@contextmanager
def running_server(monkeypatch, classifier=None, slots=3):
    monkeypatch.setattr(server_module, "MAX_PENDING_EVALUATIONS", slots)
    if classifier is None:
        classifier = lambda *args, **kwargs: {"status":"ALLOW","reason":"safe","score":0.1,"category":"benign"}
    monkeypatch.setattr(server_module, "check_user_input", classifier)
    monkeypatch.setattr(server_module, "log_request", lambda *args: None)
    client = MagicMock()
    client.collection_exists.return_value = True
    server = ThreadingHTTPServer(("127.0.0.1",0),server_module.make_handler(MagicMock(),client,object()))
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", client
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def post(url, payload):
    request = Request(url+"/check",data=json.dumps(payload).encode(),
                      headers={"Content-Type":"application/json"})
    with urlopen(request,timeout=10) as response:
        return response.status,json.load(response)


@pytest.mark.parametrize("history", [
    "bad", [None], [{"content":3}], [{"role":[],"content":"text"}],
    [{"role":"system","content":"spoofed authority"}],
    [{"role":"user","content":"turn"}] * 7,
])
def test_invalid_history_is_rejected_before_classification(monkeypatch, history):
    classifier = MagicMock()
    with running_server(monkeypatch,classifier) as (url,_):
        with pytest.raises(HTTPError) as error:
            post(url,{"prompt":"hello","history":history})
        assert error.value.code == 400
        classifier.assert_not_called()


def test_analytics_failure_preserves_completed_verdict(monkeypatch):
    with running_server(monkeypatch) as (url,_):
        def unavailable(*args):
            raise sqlite3.OperationalError("database locked")
        monkeypatch.setattr(server_module,"log_request",unavailable)
        status,result=post(url,{"prompt":"hello"})
        assert status == 200 and result["status"] == "ALLOW"
        assert result["telemetry"]["status"] == "error"


def test_context_limit_is_reported_as_request_error(monkeypatch):
    classifier=lambda *args,**kwargs: {
        "status":"ERROR","reason":"too much context","error_code":"context_limit"
    }
    with running_server(monkeypatch,classifier) as (url,_):
        with pytest.raises(HTTPError) as error:
            post(url,{"prompt":"hello"})
        assert error.value.code == 413


def test_health_detects_unavailable_qdrant(monkeypatch):
    with running_server(monkeypatch) as (url,client):
        client.collection_exists.side_effect=RuntimeError("database offline")
        with pytest.raises(HTTPError) as error:
            urlopen(url+"/health",timeout=5)
        assert error.value.code == 503
        assert json.load(error.value)["vector_store"] == "unavailable"


def test_deep_json_and_oversized_headers_are_rejected(monkeypatch):
    with running_server(monkeypatch) as (url,_):
        request=Request(url+"/check",data=b"["*2000+b"]"*2000,
                        headers={"Content-Type":"application/json"})
        with pytest.raises(HTTPError) as error:
            urlopen(request,timeout=5)
        assert error.value.code == 400
        connection=http.client.HTTPConnection("127.0.0.1",int(url.rsplit(":",1)[1]),timeout=5)
        try:
            connection.request("POST","/check",body=b"{}",headers={"Content-Length":"65537"})
            assert connection.getresponse().status == 413
        finally:
            connection.close()


def test_busy_queue_rejects_extra_work_and_releases_slots(monkeypatch):
    entered,release=threading.Event(),threading.Event()
    def classify(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        return {"status":"ALLOW","reason":"safe","score":0,"category":"benign"}
    with running_server(monkeypatch,classify,slots=1) as (url,_), ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(post,url,{"prompt":"first"})
        try:
            assert entered.wait(3)
            with pytest.raises(HTTPError) as error:
                post(url,{"prompt":"second"})
            assert error.value.code == 429
        finally:
            release.set()
        assert first.result(timeout=5)[1]["status"] == "ALLOW"
        assert post(url,{"prompt":"third"})[1]["status"] == "ALLOW"


def test_existing_dataset_vectors_do_not_reembed_or_upsert():
    prompts=["a complete known dataset signature for testing"]
    import hashlib
    pid=int(hashlib.md5(prompts[0].encode()).hexdigest(),16) % (2**63)
    client,executor=MagicMock(),MagicMock()
    client.retrieve.return_value=[SimpleNamespace(id=pid)]
    assert database._upsert_dataset_prompts(prompts,client,executor)==0
    executor.submit.assert_not_called()
    client.upsert.assert_not_called()


def test_bad_embedding_batch_leaves_resume_state_incomplete_and_closes_resources(monkeypatch,tmp_path):
    marker=tmp_path/"state.json"
    marker.write_text('{"complete":false}')
    client,executor=MagicMock(),MagicMock()
    client.collection_exists.return_value=True
    client.retrieve.return_value=[]
    executor.submit.return_value.result.return_value=[]
    monkeypatch.setattr(database,"_INGESTION_STATE",marker)
    monkeypatch.setattr(database,"FORCE_REBUILD",False)
    monkeypatch.setattr(database,"QdrantClient",lambda *args,**kwargs:client)
    monkeypatch.setattr(database,"_make_embedding_executor",lambda device:executor)
    monkeypatch.setattr(database,"_load_dataset_prompts",lambda: (["a dataset signature"],{}))
    with pytest.raises(ValueError,match="embedding count"):
        database.setup_database_and_embed()
    assert json.loads(marker.read_text())["complete"] is False
    client.upsert.assert_not_called()
    client.close.assert_called_once()
    executor.shutdown.assert_called_once()


def test_completed_index_starts_only_runtime_executor(monkeypatch,tmp_path):
    marker=tmp_path/"state.json";marker.write_text('{"complete":true}')
    client,executor=MagicMock(),MagicMock()
    client.collection_exists.return_value=True
    client.get_collection.return_value.points_count=5
    factory=MagicMock(return_value=executor)
    monkeypatch.setattr(database,"_INGESTION_STATE",marker)
    monkeypatch.setattr(database,"FORCE_REBUILD",False)
    monkeypatch.setenv("EMBEDDING_DEVICE","cpu")
    monkeypatch.setenv("INGESTION_DEVICE","cuda")
    monkeypatch.setattr(database,"QdrantClient",lambda *args,**kwargs:client)
    monkeypatch.setattr(database,"_make_embedding_executor",factory)
    monkeypatch.setattr(database,"_load_dataset_prompts",MagicMock(side_effect=AssertionError("unexpected ingestion")))
    assert database.setup_database_and_embed()==(executor,client)
    factory.assert_called_once_with("cpu")


def test_refresh_wrapper_does_not_change_serving_offline_environment(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE","1")
    pool=MagicMock()
    pool.__enter__.return_value=pool
    pool.submit.return_value.result.return_value={"new_signatures":0}
    monkeypatch.setattr(database.concurrent.futures,"ProcessPoolExecutor",lambda **kwargs:pool)
    assert database.update_datasets_job()["new_signatures"]==0
    assert os.environ["HF_HUB_OFFLINE"]=="1"
    assert pool.submit.call_args.args == (database._update_datasets_worker,database.QDRANT_URL)


def test_cached_csvs_load_without_network_and_failed_refresh_is_reported(monkeypatch,tmp_path):
    import datasets
    folder=tmp_path/"datasets";folder.mkdir()
    pd.DataFrame({"text":["a malicious sample with sufficient words"],"label":[1]}).to_csv(
        folder/"deepset_fixture_train.csv",index=False)
    loader=MagicMock(side_effect=RuntimeError("network unavailable"))
    monkeypatch.setattr(database,"PROJECT_ROOT",tmp_path)
    monkeypatch.setattr(database,"HF_DATASETS",{"deepset/fixture":None})
    monkeypatch.setattr(datasets,"load_dataset",loader)
    prompts,report=database._load_dataset_prompts()
    assert len(prompts)==1 and report["datasets_refreshed"]==0
    loader.assert_not_called()
    with pytest.raises(RuntimeError,match="No datasets refreshed"):
        database._load_dataset_prompts(refresh=True)
    assert not list(folder.glob("*.tmp"))


def test_analytics_schema_upgrade_preserves_history(monkeypatch,tmp_path):
    from src import analytics
    path=tmp_path/"history.db"
    with closing(sqlite3.connect(path)) as conn,conn:
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, prompt TEXT, status TEXT, reason TEXT, score REAL, latency_ms REAL)")
        conn.execute("INSERT INTO requests (prompt,status,latency_ms) VALUES ('legacy history','ALLOW',10)")
    monkeypatch.setattr(analytics,"DB_PATH",path)
    analytics.init_db()
    analytics.log_request("new attack","BLOCK","intent",0.2,20,"code_execution")
    stats=analytics.get_analytics_stats()
    assert stats["total_requests"]==2 and stats["allowed_requests"]==stats["blocked_requests"]==1
    assert stats["top_attack_categories"]==[{"category":"code_execution","count":1}]
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT prompt FROM requests WHERE id=1").fetchone()[0]=="legacy history"
        assert conn.execute("SELECT timestamp FROM requests WHERE id=2").fetchone()[0].endswith("+00:00")


def test_manual_update_skips_model_startup(monkeypatch):
    from src import main
    monkeypatch.setattr(main.sys,"argv",["firewall","--update-datasets"])
    setup=MagicMock(side_effect=AssertionError("should not load inference resources"))
    update=MagicMock()
    monkeypatch.setattr(main,"setup_database_and_embed",setup)
    monkeypatch.setattr(main,"update_datasets_job",update)
    main.main()
    update.assert_called_once_with()
    setup.assert_not_called()


@pytest.mark.parametrize("failure",["model","warmup","qwen_warmup","server"])
def test_startup_and_server_failures_cleanup_resources(monkeypatch,failure):
    from src import main
    pool,client,llm=MagicMock(),MagicMock(),MagicMock()
    pool.submit.return_value.result.return_value=1024
    monkeypatch.setattr(main.sys,"argv",["firewall"])
    monkeypatch.setattr(main,"ENABLE_DATASET_UPDATES",False)
    monkeypatch.setattr(main,"setup_database_and_embed",lambda:(pool,client))
    monkeypatch.setattr(main,"setup_llm",lambda:None if failure=="model" else llm)
    serve=MagicMock(side_effect=RuntimeError("server failure"))
    monkeypatch.setattr(main,"run_server",serve)
    if failure=="warmup":
        pool.submit.return_value.result.side_effect=RuntimeError("embedding failed")
    if failure=="qwen_warmup":
        monkeypatch.setattr(main,"warm_llm",MagicMock(side_effect=RuntimeError("Qwen warmup failed")))
    with pytest.raises(RuntimeError):
        main.main()
    pool.shutdown.assert_called_once_with(wait=True,cancel_futures=True)
    client.close.assert_called_once()
    if failure!="model":
        llm.close.assert_called_once()
    if failure!="server":
        serve.assert_not_called()


@pytest.mark.parametrize("headers,code", [
    ({"Content-Type":"text/plain"},415),
    ({"Content-Type":"application/json","Origin":"https://untrusted.example.test"},403),
    ({"Content-Type":"application/json","Host":"untrusted.example.test"},403),
])
def test_untrusted_browser_requests_cannot_trigger_classification(monkeypatch,headers,code):
    classifier=MagicMock()
    with running_server(monkeypatch,classifier) as (url,_):
        req=Request(url+"/check",data=b'{"prompt":"hello"}',headers=headers)
        with pytest.raises(HTTPError) as error:
            urlopen(req,timeout=5)
        assert error.value.code==code
        classifier.assert_not_called()


def test_server_close_waits_for_inflight_model_calls(monkeypatch):
    entered,release,closed=threading.Event(),threading.Event(),threading.Event()
    def classify(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        return {"status":"ALLOW","reason":"safe","score":0}
    monkeypatch.setattr(server_module,"check_user_input",classify)
    monkeypatch.setattr(server_module,"log_request",lambda *args:None)
    service=server_module.FirewallHTTPServer(("127.0.0.1",0),
        server_module.make_handler(MagicMock(),MagicMock(),object()))
    serve_thread=threading.Thread(target=service.serve_forever,daemon=True)
    serve_thread.start()
    closer=None
    with ThreadPoolExecutor(max_workers=1) as pool:
        request=pool.submit(post,f"http://127.0.0.1:{service.server_port}",{"prompt":"request"})
        try:
            assert entered.wait(3)
            service.shutdown()
            def close():
                service.server_close()
                closed.set()
            closer=threading.Thread(target=close,daemon=True)
            closer.start()
            assert not closed.wait(0.1)
        finally:
            release.set()
            service.shutdown()
            if closer is None:
                service.server_close()
        assert request.result(timeout=5)[1]["status"]=="ALLOW"
        assert closed.wait(3)
    serve_thread.join()
    closer.join()
