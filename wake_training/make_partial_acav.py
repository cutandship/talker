# -*- coding: utf-8 -*-
"""Спасение недокачанного ACAV: обрезать .incomplete до целых строк и
переписать npy-заголовок. 2.2 ГБ частичной загрузки = ~750 тыс. строк
негативных фич (~260 часов) — рабочий набор для первой тренировки, полные
17.3 ГБ не нужны.

Запуск:  python make_partial_acav.py
Выход:   data/acav_partial.npy  (+ строка для configs/*.yml)
ВАЖНО: останови качалку ACAV (dl_loop) перед запуском, чтобы читать
стабильный файл; после — можно снова запускать.
"""
import ast
import re
import shutil
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = DATA / "acav_partial.npy"


def main() -> None:
    inc = sorted(DATA.rglob("*.incomplete"), key=lambda f: f.stat().st_size)
    if not inc:
        raise SystemExit("Нет .incomplete — нечего спасать (или уже докачан?)")
    src = inc[-1]
    size = src.stat().st_size
    with open(src, "rb") as f:
        head = f.read(128)
        if head[:6] != b"\x93NUMPY":
            raise SystemExit("Это не начало npy — chunk не с нулевого байта")
        m = re.search(rb"\{.*\}", head)
        meta = ast.literal_eval(m.group(0).decode("latin1"))
        descr, shape = meta["descr"], meta["shape"]
        assert descr == "<f2" and shape[1:] == (16, 96), meta
        row = 16 * 96 * 2
        n = (size - 128) // row - 1          # минус строка на всякий случай
        if n < 10_000:
            raise SystemExit(f"Слишком мало строк ({n}) — спасать нечего")
        new_meta = ("{'descr': '<f2', 'fortran_order': False, "
                    f"'shape': ({n}, 16, 96), }}")
        # npy v1: magic(6)+ver(2)+hlen(2)+dict, всё кратно 64, конец — '\n'
        base = 6 + 2 + 2
        pad = 64 - (base + len(new_meta) + 1) % 64
        header_dict = new_meta + " " * pad + "\n"
        hlen = len(header_dict)
        out_header = (b"\x93NUMPY\x01\x00" + hlen.to_bytes(2, "little")
                      + header_dict.encode("latin1"))
        assert len(out_header) % 64 == 0

        with open(OUT, "wb") as o:
            o.write(out_header)
            f.seek(128)
            remaining = n * row
            while remaining > 0:
                chunk = f.read(min(1 << 22, remaining))
                if not chunk:
                    break
                o.write(chunk)
                remaining -= len(chunk)

    import numpy as np
    arr = np.load(OUT, mmap_mode="r")
    print(f"OK: {OUT.name} shape={arr.shape} dtype={arr.dtype} "
          f"({OUT.stat().st_size / (1 << 30):.2f} GB)")
    print("В configs/*.yml: ACAV100M_sample: \"./data/acav_partial.npy\"")


if __name__ == "__main__":
    main()
