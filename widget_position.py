"""Позиционирование плавающей пилюли и её сателлитов (концепт 36, часть B).

Чистая геометрия, без tkinter — берёт целые числа, возвращает координаты.
Логика «мозгов» центрования живёт здесь, чтобы FlowBar в ui.py подключался
минимальными вызовами (та самая «одна правка»).

Модель позиции: вместо сырых (pos_x, pos_y) храним **anchor + offset**.
- anchor — одна из 9 зон экрана ('bottom-center' и т.д.) либо 'free';
- для зон offset — небольшая ручная поправка от якоря (обычно 0);
- для 'free' offset — это абсолютная (x, y) левого-верхнего угла окна.
Так позиция переживает смену разрешения/монитора (зоны пересчитываются),
а ручное перетаскивание остаётся возможным ('free').

Дефолт по решению пользователя: anchor='bottom-center', но drag в любое место
(→ 'free' или snap к ближайшей зоне, если включён snap).
"""
from __future__ import annotations

# 9 зон + свободно. Значение = (fx, fy) доли экрана для ЦЕНТРА окна,
# кроме 'free'. 0=лево/верх, 0.5=центр, 1=право/низ.
ZONES: dict[str, tuple[float, float]] = {
    "top-left":      (0.0, 0.0),
    "top-center":    (0.5, 0.0),
    "top-right":     (1.0, 0.0),
    "center-left":   (0.0, 0.5),
    "center":        (0.5, 0.5),
    "center-right":  (1.0, 0.5),
    "bottom-left":   (0.0, 1.0),
    "bottom-center": (0.5, 1.0),
    "bottom-right":  (1.0, 1.0),
}
ANCHORS = tuple(ZONES.keys()) + ("free",)
DEFAULT_ANCHOR = "bottom-center"
MARGIN = 28          # отступ от краёв экрана для крайних зон
SNAP_THRESHOLD = 56  # на сколько px близко к зоне, чтобы притянуть на drag-end


def anchor_to_xy(anchor: str, w: int, h: int, sw: int, sh: int,
                 margin: int = MARGIN,
                 off_x: int = 0, off_y: int = 0) -> tuple[int, int]:
    """Левый-верхний угол окна w×h для зоны `anchor` на экране sw×sh.
    off_x/off_y — поправка (для зон) или абсолют (для 'free')."""
    if anchor == "free":
        return clamp_to_visible(off_x, off_y, w, h, sw, sh)
    fx, fy = ZONES.get(anchor, ZONES[DEFAULT_ANCHOR])
    # доступная область с полями
    x = margin + fx * (sw - 2 * margin - w)
    y = margin + fy * (sh - 2 * margin - h)
    return clamp_to_visible(int(round(x)) + off_x, int(round(y)) + off_y,
                            w, h, sw, sh)


def clamp_to_visible(x: int, y: int, w: int, h: int, sw: int, sh: int,
                     pad: int = 4) -> tuple[int, int]:
    """Загнать окно целиком в экран (не даём уехать за край)."""
    x = max(pad, min(int(x), sw - w - pad))
    y = max(pad, min(int(y), sh - h - pad))
    return x, y


def is_offscreen(x: int, y: int, w: int, h: int, sw: int, sh: int,
                 min_visible: int = 24) -> bool:
    """True, если на экране видно меньше min_visible px окна по любой оси —
    повод вернуть пилюлю в anchor (смена разрешения, отключён монитор)."""
    vis_x = min(x + w, sw) - max(x, 0)
    vis_y = min(y + h, sh) - max(y, 0)
    return vis_x < min_visible or vis_y < min_visible


def nearest_anchor(x: int, y: int, w: int, h: int, sw: int, sh: int,
                   margin: int = MARGIN,
                   threshold: int = SNAP_THRESHOLD) -> str | None:
    """Ближайшая зона к текущему положению окна, если центр окна ближе
    `threshold` px к её точке привязки. Иначе None (оставить 'free')."""
    cx, cy = x + w / 2, y + h / 2
    best, best_d = None, float("inf")
    for name in ZONES:
        ax, ay = anchor_to_xy(name, w, h, sw, sh, margin)
        acx, acy = ax + w / 2, ay + h / 2
        d = ((cx - acx) ** 2 + (cy - acy) ** 2) ** 0.5
        if d < best_d:
            best, best_d = name, d
    return best if best_d <= threshold else None


def resolve_drop(x: int, y: int, w: int, h: int, sw: int, sh: int,
                 snap: bool = True) -> tuple[str, int, int]:
    """Что сохранить после drag-end: (anchor, off_x, off_y).
    Со snap — пытаемся прилипнуть к зоне (off=0); иначе 'free' с абсолютом."""
    if snap:
        z = nearest_anchor(x, y, w, h, sw, sh)
        if z is not None:
            return z, 0, 0
    x, y = clamp_to_visible(x, y, w, h, sw, sh)
    return "free", x, y


# ── Сателлиты: бабл ✕/✓, тост «Вернуть», плашка результата ───────────────────
# Держим их одной группой у пилюли с коллизийным flip (как Floating-UI shift/flip),
# чтобы не разбегались по экрану.

def dock_beside(pill_x: int, pill_y: int, pill_w: int, pill_h: int,
                sat_w: int, sat_h: int, sw: int, sh: int,
                gap: int = 10, side: str = "right") -> tuple[int, int]:
    """Сателлит сбоку от пилюли, та же базовая линия по верху.
    side='right'|'left'; авто-flip, если не влезает."""
    right_x = pill_x + pill_w + gap
    left_x = pill_x - sat_w - gap
    if side == "left":
        x = left_x if left_x >= 4 else right_x
    else:
        x = right_x if right_x + sat_w <= sw - 4 else left_x
    x = max(4, min(x, sw - sat_w - 4))
    y = max(8, min(pill_y, sh - sat_h - 8))
    return x, y


def dock_above(pill_x: int, pill_y: int, pill_w: int, pill_h: int,
               sat_w: int, sat_h: int, sw: int, sh: int,
               gap: int = 8) -> tuple[int, int]:
    """Сателлит над пилюлей по центру; если не влезает сверху — под ней."""
    x = pill_x + (pill_w - sat_w) // 2
    y = pill_y - sat_h - gap
    if y < 8:
        y = pill_y + pill_h + gap
    x = max(8, min(x, sw - sat_w - 8))
    y = max(8, min(y, sh - sat_h - 8))
    return x, y


# ── Самопроверка: `python widget_position.py` ───────────────────────────────
if __name__ == "__main__":
    SW, SH, W, H = 1920, 1080, 140, 60

    # bottom-center реально снизу-по-центру
    x, y = anchor_to_xy("bottom-center", W, H, SW, SH)
    assert abs((x + W / 2) - SW / 2) < 2, (x, "не по центру X")
    assert y + H <= SH and y > SH * 0.7, (y, "не снизу")

    # всё в пределах экрана
    for a in ZONES:
        ax, ay = anchor_to_xy(a, W, H, SW, SH)
        assert 0 <= ax <= SW - W and 0 <= ay <= SH - H, (a, ax, ay)

    # clamp загоняет внутрь
    assert clamp_to_visible(-500, 5000, W, H, SW, SH) == (4, SH - H - 4)

    # offscreen-детект
    assert is_offscreen(-200, 100, W, H, SW, SH) is True
    assert is_offscreen(800, 500, W, H, SW, SH) is False

    # drop рядом с bottom-center → прилипает к зоне
    bx, by = anchor_to_xy("bottom-center", W, H, SW, SH)
    assert resolve_drop(bx + 10, by + 8, W, H, SW, SH)[0] == "bottom-center"

    # drop в случайной серёдке → free + абсолют
    a, ox, oy = resolve_drop(700, 400, W, H, SW, SH)
    assert a == "free" and (ox, oy) == (700, 400)

    # flip сателлита у правого края
    fx, _ = dock_beside(SW - W - 4, 900, W, H, 120, 50, SW, SH, side="right")
    assert fx < SW - W, "сателлит не сделал flip влево"

    print("widget_position: все проверки пройдены OK")
