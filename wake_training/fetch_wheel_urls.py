# -*- coding: utf-8 -*-
"""Выяснить точные URL cp311/win_amd64-wheels через PyPI JSON API (мелкие
запросы проходят даже по задушенному каналу). Печатает: <файл> <url>."""
import json
import urllib.request

PKGS = ["numba", "llvmlite", "audiomentations", "librosa", "soxr",
        "speechbrain", "hyperpyyaml", "sentencepiece", "pronouncing"]


def best_wheel(pkg: str, version: str | None = None):
    url = f"https://pypi.org/pypi/{pkg}/json"
    if version:
        url = f"https://pypi.org/pypi/{pkg}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    files = (data["urls"] if version else
             data["releases"][data["info"]["version"]])
    win = [f for f in files if f["filename"].endswith(".whl")
           and ("cp311" in f["filename"] or "py3-none-any" in f["filename"])
           and ("win_amd64" in f["filename"] or "any" in f["filename"])]
    if not win:
        return None
    # точная cp311-сборка приоритетнее universal
    win.sort(key=lambda f: ("cp311" not in f["filename"], len(f["filename"])))
    return win[0]


if __name__ == "__main__":
    # совместимая пара numba ↔ llvmlite: берём актуальную numba и её pin
    nb = best_wheel("numba")
    print(nb["filename"], nb["url"])
    # llvmlite pin для numba 0.6x — просто свежий (pip проверит при установке)
    lv = best_wheel("llvmlite")
    print(lv["filename"], lv["url"])
    for pkg in ("speechbrain",):
        w = best_wheel(pkg, "0.5.14")
        if w:
            print(w["filename"], w["url"])
    for pkg in ("audiomentations", "librosa", "soxr", "hyperpyyaml",
                "sentencepiece", "pronouncing"):
        w = best_wheel(pkg)
        if w:
            print(w["filename"], w["url"])
    # torchaudio под наш torch 2.6.0+cu124 — фиксированный шаблон pytorch-индекса
    print("torchaudio-2.6.0+cu124-cp311-cp311-win_amd64.whl "
          "https://download.pytorch.org/whl/cu124/"
          "torchaudio-2.6.0%2Bcu124-cp311-cp311-win_amd64.whl")
