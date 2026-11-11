from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from pathlib import Path

import pytest

from archive_builder.builder import downloader
from archive_builder.builder.downloader import _source_identity, _write_capture, download_and_extract_pages, download_pages
from archive_builder.cdx.client import ReplayBytes
from archive_builder.config import ExtractionRule, NetworkConfig, new_project_config
from archive_builder.database.connection import open_database
from archive_builder.database.repositories import upsert_inventory_rows, upsert_representative_rows
from archive_builder.events import Stopped
from archive_builder.utils import safe_filename_for_url, sha256_bytes


class FakeGate:
    def __init__(self):
        self.starts = 0
    @property
    def starts_per_second(self):
        return float(self.starts)


class FakeWaybackClient:
    slow_done = threading.Event()
    network_calls = 0
    def __init__(self, network, stop_event, delay=None, **kwargs):
        self.stop_event = stop_event
        self.gate = FakeGate()
    def close(self):
        pass
    def stream_bytes(self, url, max_bytes, **kwargs):
        type(self).network_calls += 1
        self.gate.starts += 1
        if "media/2/" in url:
            time.sleep(0.25)
            type(self).slow_done.set()
        html = f"<html><head><title>{url[-30:]}</title></head><body>Posted: 01/02/2008</body></html>".encode()
        return ReplayBytes(
            html, 200,
            "https://web.archive.org/web/20080101000000id_/http://example.com/",
            "text/html; charset=utf-8", {"content-type":"text/html; charset=utf-8"},
        )


def _prepare_pages(db, count: int):
    inv=[]; rep=[]
    for i in range(1,count+1):
        url=f"http://example.com/media/{i}/"
        key=f"com,example)/media/{i}/"
        inv.append({"urlkey":key,"original":url,"timestamp":"20080101000000","mimetype":"text/html","statuscode":"200"})
        rep.append({"urlkey":key,"original":url,"timestamp":"20080101000000","mimetype":"text/html","statuscode":"200","digest":"D","length":"100"})
    upsert_inventory_rows(db, inv)
    upsert_representative_rows(db, rep)
    db.commit()


def _cfg(tmp_path: Path, workers=2):
    cfg=new_project_config(tmp_path)
    cfg.site.domain="example.com"
    cfg.site.content_path="/media/"
    cfg.site.include_subdomains=False
    cfg.site.day_first_dates=True
    cfg.site.rules["posted_date"] = ExtractionRule(method="text_after", source="text", pattern="Posted:")
    cfg.network=NetworkConfig(workers=workers, download_delay=0, cdx_workers=2, cdx_delay=0)
    return cfg.normalized()


def test_combined_pipeline_commits_early_page_before_last_replay_finishes(tmp_path: Path, monkeypatch):
    FakeWaybackClient.slow_done = threading.Event()
    FakeWaybackClient.network_calls = 0
    monkeypatch.setattr(downloader, "WaybackClient", FakeWaybackClient)
    cfg=_cfg(tmp_path, workers=2)
    db=open_database(tmp_path)
    _prepare_pages(db,2)
    observed=[]
    try:
        def cb(event):
            if event.stage == "build" and event.current == 1:
                complete=db.execute("SELECT COUNT(*) n FROM pages WHERE extraction_state='complete'").fetchone()["n"]
                observed.append((complete, FakeWaybackClient.slow_done.is_set()))
        download_and_extract_pages(cfg,db,threading.Event(),cb)
        assert observed
        assert observed[0][0] >= 1
        assert observed[0][1] is False
        assert db.execute("SELECT COUNT(*) n FROM pages WHERE extraction_state='complete'").fetchone()["n"] == 2
    finally:
        db.close()


def test_combined_executor_submission_window_is_bounded(tmp_path: Path, monkeypatch):
    FakeWaybackClient.network_calls = 0
    monkeypatch.setattr(downloader, "WaybackClient", FakeWaybackClient)
    state={"outstanding":0,"max":0}
    class CountingExecutor(RealThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            state["outstanding"] += 1
            state["max"] = max(state["max"], state["outstanding"])
            fut=super().submit(*args, **kwargs)
            def done(_):
                state["outstanding"] -= 1
            fut.add_done_callback(done)
            return fut
    monkeypatch.setattr(downloader, "ThreadPoolExecutor", CountingExecutor)
    cfg=_cfg(tmp_path, workers=3)
    db=open_database(tmp_path)
    _prepare_pages(db,50)
    try:
        download_and_extract_pages(cfg,db,threading.Event())
        assert state["max"] <= 6
        assert db.execute("SELECT COUNT(*) n FROM pages WHERE extraction_state='complete'").fetchone()["n"] == 50
    finally:
        db.close()


def test_atomic_capture_is_adopted_without_network_after_commit_gap(tmp_path: Path, monkeypatch):
    class NoNetwork(FakeWaybackClient):
        def stream_bytes(self,*a,**k):
            raise AssertionError("network should not be used when a valid staged capture exists")
    monkeypatch.setattr(downloader, "WaybackClient", NoNetwork)
    cfg=_cfg(tmp_path, workers=1)
    db=open_database(tmp_path)
    _prepare_pages(db,1)
    row=db.execute("SELECT id,original_url,selected_capture_timestamp FROM pages").fetchone()
    timestamp=row["selected_capture_timestamp"]
    rel=Path("captures")/safe_filename_for_url(row["original_url"],timestamp)
    dest=tmp_path/rel
    data=b"<html><head><title>adopt me</title></head><body>Posted: 01/02/2008</body></html>"
    digest=sha256_bytes(data)
    metadata={
        "selected_capture_timestamp":timestamp,
        "actual_replay_timestamp":timestamp,
        "final_url":f"https://web.archive.org/web/{timestamp}id_/{row['original_url']}",
        "http_status":200,"response_mimetype":"text/html","response_encoding":"utf-8",
        "content_classification":"known_text","classification_reason":"test",
        "source_size":len(data),"source_sha256":digest,"source_identity":_source_identity(timestamp,digest),
    }
    _write_capture(dest,data,metadata)
    try:
        download_pages(cfg,db,threading.Event())
        saved=db.execute("SELECT download_state,local_path,source_sha256 FROM pages").fetchone()
        assert saved["download_state"] == "complete"
        assert saved["local_path"] == str(rel)
        assert saved["source_sha256"] == digest
    finally:
        db.close()


def test_changed_rule_reextracts_complete_local_source_without_network(tmp_path: Path, monkeypatch):
    FakeWaybackClient.network_calls=0
    monkeypatch.setattr(downloader,"WaybackClient",FakeWaybackClient)
    cfg=_cfg(tmp_path,workers=1)
    db=open_database(tmp_path)
    _prepare_pages(db,1)
    try:
        download_and_extract_pages(cfg,db,threading.Event())
        first_calls=FakeWaybackClient.network_calls
        assert first_calls == 1
        cfg.site.rules["title"] = ExtractionRule(method="css_selector", source="text", pattern="title")
        class NoNetwork(FakeWaybackClient):
            def stream_bytes(self,*a,**k):
                raise AssertionError("changed rules should reuse the local source")
        monkeypatch.setattr(downloader,"WaybackClient",NoNetwork)
        download_and_extract_pages(cfg,db,threading.Event())
        assert db.execute("SELECT extraction_state FROM pages").fetchone()["extraction_state"] == "complete"
    finally:
        db.close()


def test_stop_before_queueing_leaves_no_active_states(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(downloader,"WaybackClient",FakeWaybackClient)
    cfg=_cfg(tmp_path,workers=2)
    db=open_database(tmp_path)
    _prepare_pages(db,5)
    stop=threading.Event(); stop.set()
    try:
        with pytest.raises(Stopped):
            download_and_extract_pages(cfg,db,stop)
        active=db.execute("SELECT COUNT(*) n FROM pages WHERE download_state='downloading' OR extraction_state='extracting'").fetchone()["n"]
        assert active == 0
    finally:
        db.close()
