# -*- coding: utf-8 -*-
"""Смоук локального API + веб-окон (api_server.py / web_ui.py): сервер
поднимается, /ui отдаёт страницу, эндпоинты защищены токеном, сохранение
конфига через POST реально пишет на диск (во временный файл)."""
import json
import time
import types
import urllib.error
import urllib.request

import pytest

import api_server
import config as config_mod


class _FakeHistory:
    def __init__(self):
        self._e = [{"timestamp": "2026-06-10T12:00:00", "text": "проверка раз"},
                   {"timestamp": "2026-06-11T09:30:00", "text": "проверка два"}]

    def entries(self):
        return self._e

    def export_text(self):
        return "\n\n".join(e["text"] for e in self._e)

    def clear(self):
        self._e = []


@pytest.fixture(scope="module")
def server():
    fake = types.SimpleNamespace(
        config=config_mod.Config(), history=_FakeHistory(),
        transcriber=None, cleaner_chain=None, _whisper_mode=False,
        root=None, _mic_monitor=None, _snd=None,
        _hooks_pause=lambda: None, _register_hooks=lambda: None)
    srv = api_server.ApiServer(fake, port=7891)
    srv.start()
    base = f"http://127.0.0.1:{srv.actual_port}"
    # ждём готовности (uvicorn стартует в фоне)
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/ui", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.fail("api server did not start")
    yield base, api_server.read_token(), fake
    srv.stop()


def _get(base, path, token=None):
    req = urllib.request.Request(
        base + path,
        headers={"Authorization": f"Bearer {token}"} if token else {})
    return urllib.request.urlopen(req, timeout=5)


def test_ui_page_served(server):
    base, _tok, _ = server
    r = _get(base, "/ui")
    assert r.status == 200
    assert b"Talker" in r.read()


def test_endpoints_require_token(server):
    base, _tok, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base, "/ui/api/state")
    assert e.value.code == 401


def test_state_and_history(server):
    base, tok, _ = server
    d = json.load(_get(base, "/ui/api/state", tok))
    assert "config" in d and "hotkey" in d["config"]
    d = json.load(_get(base, "/ui/api/history", tok))
    assert len(d["entries"]) == 2


def test_history_export(server):
    base, tok, _ = server
    r = _get(base, f"/ui/api/history/export?fmt=txt&token={tok}")
    assert r.status == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")


def test_config_post_saves(server, tmp_path, monkeypatch):
    base, tok, _ = server
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    payload = {"engine": "gigaam", "media_guard": False,
               "vocabulary": ["Гигачад", "Иннополис"]}
    req = urllib.request.Request(
        base + "/ui/api/config", data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json"}, method="POST")
    r = urllib.request.urlopen(req, timeout=5)
    assert json.load(r)["ok"] is True
    loaded = config_mod.load_config()
    assert loaded.stt.engine == "gigaam"
    assert loaded.continuous.media_guard is False
    assert loaded.vocabulary.words == ["Гигачад", "Иннополис"]
