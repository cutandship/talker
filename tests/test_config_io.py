# -*- coding: utf-8 -*-
"""config.py: сериализация TOML устойчива к спецсимволам, update_config —
merge-safe (частичная запись не затирает чужие поля)."""
import pytest

import config as config_mod
from config import Config, SnippetConfig, load_config, save_config, update_config


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Уводим CONFIG_PATH во временный файл — тесты не трогают живой конфиг."""
    p = tmp_path / "config.toml"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", p)
    return p


def test_roundtrip_defaults(tmp_config):
    cfg = Config()
    save_config(cfg)
    loaded = load_config()
    assert loaded.stt.engine == cfg.stt.engine
    assert loaded.hotkey.key == cfg.hotkey.key
    assert loaded.continuous.media_guard == cfg.continuous.media_guard


def test_roundtrip_hostile_strings(tmp_config):
    """Кавычки, бэкслэши и переводы строк не должны ломать TOML."""
    cfg = Config()
    cfg.hotkey.key = 'right "alt" \\ test'
    cfg.vocabulary.words = ['сло"во', "путь\\к\\файлу"]
    cfg.snippets = [SnippetConfig(trigger="подпись",
                                  body='Привет,\n"Георгий"\\конец')]
    save_config(cfg)
    loaded = load_config()
    assert loaded.hotkey.key == cfg.hotkey.key
    assert loaded.vocabulary.words == cfg.vocabulary.words
    assert loaded.snippets[0].body == cfg.snippets[0].body


def test_update_config_merges(tmp_config):
    """Два независимых писателя: частичное обновление НЕ затирает чужое."""
    cfg = Config()
    cfg.vocabulary.words = ["Иннополис"]
    save_config(cfg)
    # писатель А меняет только позицию виджета
    update_config(lambda c: setattr(c.widget, "scale", 1.5))
    # писатель Б меняет только тему
    update_config(lambda c: setattr(c.ui, "theme", "light"))
    loaded = load_config()
    assert loaded.widget.scale == 1.5
    assert loaded.ui.theme == "light"
    assert loaded.vocabulary.words == ["Иннополис"]   # никто не потерял словарь


def test_corrupt_config_falls_back(tmp_config):
    tmp_config.write_text("это не toml [[[", encoding="utf-8")
    loaded = load_config()          # не должен упасть
    assert loaded.stt.engine in ("whisper", "gigaam")
