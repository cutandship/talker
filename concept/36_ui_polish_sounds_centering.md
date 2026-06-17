# 36. UI-полировка внешней диктовки: звуки старт/стоп, центрование виджета, мелочи против дёрганья

> Подтянуть «внешний» поток (диктовка поверх чужих окон) к уровню Wispr Flow / superwhisper: звуковые earcon'ы на старт/стоп/пустой результат, осмысленное центрование **пилюли** и её сателлитов, и пара мелочей против «попапа/дёрганья» UI. Всё аддитивно — основной код не ломаем.

**Категория:** UX потока ввода — полировка, дёшево, высокая ежедневная отдача.
**Эффект:** 🔥 заметность высокая, риск низкий (новые опции, дефолты можно держать выключенными).
**Готовность концепта:** 🟢 High — решения по дефолтам приняты (см. «Решения»); логика вынесена в два standalone-модуля, основной код не тронут, остаётся «одна правка» подключения.
**Источник идеи:** ручной research 2026-06-03 — [superwhisper changelog](https://superwhisper.com/changelog), [Wispr Flow docs](https://docs.wisprflow.ai/), earcon-исследования. Ориентир терминов — словарь панелей из этой сессии.
**Статус:** `sounds.py` + `widget_position.py` написаны и проходят самотесты. Основной код (ui.py/main.py/config.py) **не трогали** — его правит другой агент; подключение — отдельной правкой ниже.

---

## Словарь (зафиксирован в этой сессии)

| Код | Имя | Что |
|---|---|---|
| `FlowBar` [ui.py:481](../ui.py) | **пилюля** | плавающая капсула, всегда на экране; состояния `loading/idle/recording/listening/processing/error` |
| `ControlBubble` [ui.py:1446](../ui.py) | **бабл ✕/✓** | прилипает к пилюле при записи: ✕ отмена, ✓ стоп+вставка |
| `PasteFallbackBubble` [ui.py:1704](../ui.py) | **плашка результата** | всплывает после диктовки: текст + Копировать + ✎ Поправить |
| `CancelUndoToast` [ui.py:2044](../ui.py) | **тост «Вернуть»** | после ✕: «Отменено» + полоска-таймер + ↶ Вернуть |

Звук «перед началом» = переход **idle→recording**; звук «в конце» = **recording→processing** / факт вставки.

---

## A. Звуки старт / стоп (earcons) 🟢

### Зачем
Сейчас звуковой обратной связи **нет** вообще (упоминания `sounds` в [config.py:227](../config.py) — это фонетический словарь, не аудио; единственный beep — только в концепте [21_wake_word](21_wake_word.md)). При внешней диктовке глаза заняты чужим окном, а не пилюлей — короткий звук подтверждает «пишу / закончил» без взгляда вниз.

### Что делают ориентиры
- **superwhisper:** звук на старте и на конце записи (с 2023), **темы звуков** в настройках, **pre-stop sound** (сигнал *перед* остановкой), **empty-result sound** (запись вышла пустой). Звук — отдельная подсистема с переключателем.
- **macOS Dictation:** короткий «ready»-тон при старте слушания + фирменный «whiz» в конце; курсор пульсирует.
- **Earcon-исследования:** держать **3–5 звуков максимум**, единый «грамматический» стиль; центры частот ~350–1000 Гц; длительности 100/250/500 мс; ADSR-огибающая (перкуссивная для коротких сигналов). Обязателен тумблер off (доступность, open-office).

### Технический подход
Три earcon'а, не больше:
- **старт** — короткий восходящий «чирп» (e.g. 600→900 Гц, ~120 мс, ADSR perc);
- **стоп** — нисходящий (900→600 Гц, ~140 мс);
- **пусто/ошибка** — низкий двойной (440 Гц ×2, тихий).

Генерировать **программно через numpy** (синус + ADSR) и проигрывать через уже подключённый `sounddevice` (OutputStream на default-output) — **нулевые ассеты**, громкость/частота из конфига. Альтернатива — bundle крошечных `.wav` в `assets/sounds/` + `winsound.PlaySound(..., SND_ASYNC|SND_FILENAME)` (проще, но без контроля громкости и только WAV). Рекомендую numpy-синтез: гибко и без файлов.

### 🔴 Критичный порядок с audio ducker
У Talker есть **ducker**, который при записи глушит весь вывод (`duck_mode=master`, `duck_level=0.0` = full mute, [README](00_README.md), [audio_ducker.py](../audio_ducker.py)). Значит:
- **старт-звук** играть **до** `duck()` + старта записи — иначе сам earcon будет приглушён;
- **стоп-звук** — **после** восстановления громкости (un-duck);
- если `audio.source=system` (loopback, [loopback_recorder.py](../loopback_recorder.py)) — вывод earcon'а попадёт в захват; старт играть до арма loopback, стоп после стопа стрима. **pre-stop sound из superwhisper при loopback включать нельзя** (наложится на запись) — оставить только для mic-режима или выключить совсем в v1.

### Архитектура
- **`sounds.py`** (новый) — `play(kind: 'start'|'stop'|'empty')`, ленивый синтез + кэш массивов, неблокирующий проигрыш.
- **`main.py`** — вызовы в точках старта/остановки/`_process` (рядом с duck/un-duck, в правильном порядке выше).
- **`config.py`** — `[sounds] enabled=false, volume=0.5, theme="soft"` (тема = набор частот/длительностей).
- **`ui.py`** — в Settings → «Выходные данные» тумблер + слайдер громкости + выпадашка темы (later).

### Edge cases
| Сценарий | Поведение |
|---|---|
| Ducker глушит всё | старт **до** duck, стоп **после** un-duck (см. выше). |
| Loopback-режим | earcon до арма / после стопа стрима; pre-stop отключён. |
| Быстрый старт-стоп | дебаунс: не накладывать два earcon'а, обрывать предыдущий. |
| Нет output-устройства | `try/except`, тихо без звука (как и сейчас без звука). |
| Open-office / тишина | дефолт `enabled=false`; включается одним тумблером. |

### Acceptance
- Тумблер off по умолчанию; включённый — старт/стоп слышны и **не** приглушаются собственным ducker'ом.
- В loopback earcon не попадает в транскрипт.
- Пустой результат даёт отдельный тихий сигнал.

---

## B. Центрование и позиционирование 🟡

### Зачем (что сейчас «не по центру»)
1. Пилюля по умолчанию в **правом-нижнем** углу ([ui.py:611](../ui.py)) и **растёт только вправо** — левый край зафиксирован ([ui.py:966](../ui.py) «Keep the LEFT edge fixed»). При idle→recording (W_IDLE 112 → W_ACTIVE 314, [ui.py:513](../ui.py)) визуальный центр уезжает вправо, а **бабл ✕/✓** докуется ещё правее → кластер может упереться в край, и включается реактивная `_compute_grow_zone` ([ui.py:680](../ui.py)).
2. Сателлиты висят на **разных якорях**: бабл ✕/✓ — сбоку ([ui.py:1535](../ui.py)), тост «Вернуть» — сверху/снизу пилюли ([ui.py:2148](../ui.py)), плашка результата — по центру низа **экрана** ([README](00_README.md)). Итог — они не читаются как одна группа.
3. Позиция хранится сырыми `pos_x/pos_y` ([ui.py:553](../ui.py)), привязки к экрану/монитору нет: смена разрешения или отключение монитора может «потерять» пилюлю. Центрируются при появлении только Settings/History, не сама пилюля.

Ориентиры: **Wispr Flow держит бар строго по центру снизу** (репозиционирование — только сторонним PillFloat); **Win+H** — по центру сверху; **superwhisper**: *«re-centers itself if not within screen bounds»*, *«ensure floating window is in view when presented, otherwise center it»*, snap-points для мульти-монитора. Floating-UI как теория: `flip`/`shift` (коллизии) + `autoUpdate` (держать якорь).

### Подход
1. **Пресеты + snap-зоны.** `widget.anchor ∈ {bottom-center, bottom-right, top-center, free}`. Дефолт — **bottom-center** (как Wispr) либо оставить bottom-right и добавить center опцией (см. Открытые вопросы). На отпускании drag — притягивать к ближайшей из 9 зон (углы/центры рёбер/центр), если ближе ~40 px. Хранить **anchor + смещение**, не сырые x/y → переживает смену разрешения.
2. **Рост от центра.** Для центральных якорей менять «левый край зафиксирован» на «центр зафиксирован» — idle→recording больше не «съезжает». `_compute_grow_zone` уже умеет `center`; расширить, чтобы уважала выбранный anchor.
3. **Clamp/re-center при выходе за экран** — на старте (уже есть, [ui.py:606](../ui.py)) **плюс** на смену разрешения/отключение монитора (`<Configure>` / WM_DISPLAYCHANGE) → если пилюля вне видимой области, вернуть в anchor. Зеркалит поведение superwhisper.
4. **Единый кластер сателлитов.** Бабл ✕/✓, тост «Вернуть», плашка результата якорить к **одной** базовой линии относительно пилюли с коллизийным flip (как Floating-UI `flip`/`shift`), чтобы читались одной строкой/стопкой, а не вразнобой.
5. **Мульти-монитор.** Хранить индекс монитора + относительную позицию; клампить в границы *текущего* монитора (win32 `EnumDisplayMonitors`), а не только первичного.

### Архитектура
- **`ui.py`** — `FlowBar`: геометрия окна (`_make_window`, `_tick` рост), `_persist_position`→хранить anchor+offset; `_position()` у `ControlBubble`/`CancelUndoToast`/`PasteFallbackBubble` — общая helper-функция «док к пилюле с flip».
- **`config.py`** — `[widget] anchor`, `snap=true`, (опц.) `monitor`.
- Новых зависимостей нет (win32 уже через ctypes).

### Edge cases
| Сценарий | Поведение |
|---|---|
| Смена разрешения / отключён монитор | re-clamp в anchor текущего монитора. |
| Пилюля у самого края, бабл не влезает вправо | flip влево (уже частично есть в `ControlBubble._position`). |
| Anchor=bottom-center, идёт рост | центр фиксирован, симметричный рост, не съезжает. |
| Юзер перетащил вручную (free) | snap опционален; «free» = старое поведение, ничего не ломаем. |
| Мульти-монитор разной DPI | позицию считать в координатах целевого монитора. |

### Acceptance
- Есть пресет **bottom-center**; при нём idle→recording не сдвигает пилюлю вбок.
- При выходе за экран (resolution change) пилюля сама возвращается в видимую зону.
- Сателлиты держатся одной группой у пилюли, не разбегаются по экрану.

---

## C. Мелочи против дёрганья (дёшево) 🟢

Из практики superwhisper (changelog) — низкий риск, заметный лоск:
- **Плейсхолдер-waveform в idle** — рисовать плоскую базовую линию столбиков в покое, чтобы recording не «попапил» волну с нуля (*«Placeholder for wave when in Ready state, avoid UI popping»*). Точечно в `_draw` [ui.py:1089](../ui.py).
- **Пустой результат — явный фидбэк** — сейчас при пустой транскрипции, похоже, тишина; дать тихий earcon (часть A) + краткую плашку «Пусто» (1.5 с). Связать с `_process` в [main.py](../main.py).
- **Готовность к записи** — опц. лёгкий «ready»-тон/пульс при старте слушания (паритет с macOS), если включены звуки.

### Acceptance
- Idle уже показывает базовую линию волны; вход в recording — без скачка.
- Пустая диктовка не молчит «вникуда».

---

## D. Пилюля и ховер ✕/✓ (research 2026-06-03) 🟢

### Что сейчас
- **Пилюля** на ховере: lift ~2 px + осветление + accent-halo (`_lift`, [ui.py:1110](../ui.py)), idle-точка серая→красная. База уже по best-practice (subtle lift + halo).
- **Бабл ✕/✓** ([ui.py:1487](../ui.py)): ховер = только затемнение фона (✕ `#3a2020`→`#5a2828`, ✓ `#1f8a4c`→`#26a35a`). Минусы: цвет — единственный сигнал; цели мелкие (`width=2`); ✕ и ✓ симметричны по весу.

### Принципы из источников
- **Icon-кнопки подсвечивать кольцом/фоном, а НЕ цветом глифа**; цвета мало — добавлять форму/размер (UXPin, Mockplus).
- **Деструктивное (✕) — тише** primary; **✓ — primary** (солиднее/чуть крупнее, lift на ховере) (DesignMonks, Carbon).
- **Fitts:** мелкие цели медленнее и ошибочнее → **расширить невидимую хит-зону**; «иконка + подпись» попасть легче; край экрана — «magic pixels» (NN/g, LawsOfUX).
- **Calm hierarchy 2025:** тонко, без авто-анимаций; ховер = лёгкий сдвиг цвета/тени/масштаба.
- **Микровзаимодействие** (Saffer): trigger→rules→feedback→loops; «как можно больше малым».

### Опции для ✕/✓ (ранжировано)
1. **Кольцо + микро-масштаб на ховере** вместо только фона: 2 px ring в цвете кнопки (красный ✕ / зелёный ✓) + глиф ×1.08; нажатие ×0.96 (снэппи). — канон icon-button.
2. **Расширенная хит-зона:** ловить ховер/клик на рамке вокруг глифа (квадрат ≈ высота пилюли + паддинг), не на самом «✕». — Fitts, меньше промахов по деструктивной кнопке.
3. **Подпись на ховере:** под кнопкой всплывает «Отмена» / «Готово». Иконка+текст — и попасть легче, и понятнее новичку.
4. **Асимметрия веса:** ✓ заметнее (primary), ✕ приглушён и загорается красным только на ховере. — меньше случайных отмен.
5. **Клавиши:** Enter = ✓, Esc = ✕ во время записи (+focus-ring). — keyboard/WCAG.
- Hold-to-confirm (зажать ✕) **не нужен**: уже есть тост «Вернуть» (4 c undo) как сеть безопасности.

### Опции для самой пилюли
- `cursor="hand2"` на канвасе — читается как кликабельная.
- Лёгкая discoverability-подпись на первый ховер (тултип сейчас выключен, [ui.py:861](../ui.py)).
- idle: вместо голой точки — тонкий микрофонный глиф (опц.), состояние яснее.

### Выбрано (тест-клон `control_bubble_preview.py`, 2026-06-03)
- **Ховер = «Полный»**: кольцо + расширенная хит-зона + подпись + асимметрия (✓ крупнее, ✕ приглушён и краснеет на ховере).
- **+10% на ховере растит ВСЮ кнопку** (cell + глиф, не только глиф), нажатие ×0.94 — подтверждено как нужное.
- **Размеры** (привязка к высоте пилюли PH): радиус кнопки `rb = PH×0.44`; ✓ `= rb×1.12`, ✕ `= rb×0.90`; зазор пилюля↔кнопка `PH×0.55`, ✕↔✓ `PH×0.66`; глиф `r×0.92`.
- **Пилюлю оставляем родную** (FlowBar с живой волной — пользователю нравится); «прививаем» только эти кнопки.
- **Расположение — «По бокам»** (выбрано 2026-06-03): ✕ слева, ✓ справа — пользователю «реально интуитивнее и понятнее». В клоне дефолт уже такой.

### Acceptance
- Ховер по ✕/✓ даёт кольцо + масштаб всей кнопки (+10%) + подпись, не только смену фона.
- В ✕/✓ легко попасть (расширенная зона), ✓ читается как главное действие.

---

## Сложность
- **A. Звуки:** ~3–4 ч, ~120 LOC (`sounds.py` + хуки + UI-тумблер). Главное — порядок с ducker/loopback.
- **B. Центрование:** ~5–7 ч, ~200 LOC (anchor-модель + snap + re-clamp + helper сателлитов). Самая объёмная.
- **C. Мелочи:** ~1–2 ч, ~40 LOC.

---

## Решения (зафиксированы 2026-06-03)
1. **Дефолт позиции — `bottom-center`**, drag в любое место (snap к зоне, иначе `free`). `widget_position.py`.
2. **Звуки ON по умолчанию.** В Settings — блок «Звуки»: тумблер + громкость + **выбор варианта на КАЖДОЕ событие (5 на каждое) + пресеты-комбинации + ▶ прослушать**. Готовый компонент — `sound_settings_panel.py`.
3. **Earcon — синтез** (numpy), но **играем из файла**: выбранный вариант рендерится в `assets/sounds/cache/` и проигрывается `SND_FILENAME|SND_ASYNC`. Причина — на машине пользователя `SND_MEMORY` молчал, файлы играют (✅ подтверждено). Свой звук: `assets/sounds/custom/<event>.wav` перебивает синтез.
4. **pre-stop — да**, только mic-режим (в loopback нельзя — наложится на запись).
5. **Расположение ✕/✓ — «По бокам»** (✕ слева, ✓ справа): пользователю интуитивнее, разносит деструктив и подтверждение. Ховер — «Полный» (часть D).

---

## Сделано — standalone-модули (основной код не тронут)

**`sounds.py`** — earcon-движок. **Палитра 4 события × 5 вариантов** (`PALETTE`, `variant_names`, `PRESETS`-комбинации). `SoundPlayer(enabled, volume, selection)` + `.start()/.stop()/.pre_stop()/.empty()`, `set_variant`, `update`, `from_config`. Синтез numpy (glide+ADSR) → **рендер в `assets/sounds/cache/` → `SND_FILENAME|SND_ASYNC`** (файловый проигрыш = слышимость; `SND_MEMORY` на машине молчал). Override: `assets/sounds/custom/<event>.wav`. Контракт с ducker'ом — в docstring. Самотест: `python sounds.py` / `dump` / `play`.

**`sound_settings_panel.py`** — готовый **customtkinter-блок для меню**: тумблер + громкость + пресет + по строке на событие (выпадашка 5 вариантов + ▶). Вставляется в `SettingsWindow` одной строкой (см. ниже). Демо: `python sound_settings_panel.py`.

**`control_bubble_preview.py`** — тест-клон ✕/✓ (ховер «Полный», расположение «По бокам») + аудит звука пресетами. Временный, в сборку не входит.

**`widget_position.py`** — чистая геометрия (без tkinter). Модель **anchor+offset** (9 зон + `free`); `anchor_to_xy`, `clamp_to_visible`, `is_offscreen`, `nearest_anchor`, `resolve_drop` (snap/free на drop), `dock_beside`/`dock_above` (сателлиты с flip). Дефолт `bottom-center`. Самотест: `python widget_position.py` (asserts).

---

## Подключение одной правкой (когда освободится основной код)

Точечные хуки — все вызовы коротки, логика уже в модулях.

### A. Звуки → `main.py`
- В `__init__`: `self._snd = SoundPlayer.from_config(self.config)` (после загрузки config).
- **Старт-звук** — первой строкой в `_duck_start()` [main.py:671](../main.py), ДО `self._ducker.start()`: `self._snd.start()`. Это единый choke-point старта записи (press/continuous/command) → порядок «звук → duck» соблюдён сам.
- **Стоп-звук** — последней строкой в `_duck_restore()` [main.py:678](../main.py), ПОСЛЕ `self._ducker.restore()`: `self._snd.stop()`. Единый choke-point конца записи.
- **pre_stop** — перед фактической остановкой recorder'а на release (`_on_press` release-ветка / `_stop_continuous` [main.py:854](../main.py)), только если `self.config.audio.source != "system"`: `self._snd.pre_stop()`.
- **empty** — рядом с `_backup_failed_audio(audio, "empty")` [main.py:1013](../main.py): `self._snd.empty()`.
- При изменении настроек звука в Settings — `self._snd.update(enabled=…, volume=…, theme=…)`.

### B. Центрование → `ui.py` (FlowBar) + `config.py`
- `config.py`: расширить `WidgetConfig` — `anchor="bottom-center"`, `off_x=0`, `off_y=0`, `snap=True` (старые `pos_x/pos_y` → читать как `free`-миграцию). Добавить `SoundsConfig(enabled=True, volume=0.9, start="Восходящий", stop="Нисходящий", pre_stop="Тик", empty="Два низких")` + `[sounds]` в save/load + поле `sounds` на `Config`. (Дефолты = `sounds.DEFAULT_SELECTION`.)
- `ui.py` `FlowBar._make_window` [ui.py:594](../ui.py): дефолтную позицию (хардкод bottom-right, [ui.py:611](../ui.py)) заменить на `widget_position.anchor_to_xy(cfg.anchor, full_w, full_h, sw, sh, off_x=cfg.off_x, off_y=cfg.off_y)`.
- `_persist_position` [ui.py:758](../ui.py): вместо сырых x/y сохранять `resolve_drop(x, y, full_w, full_h, sw, sh, snap=cfg.snap)` → `anchor/off_x/off_y`.
- Re-clamp при смене экрана: в `_tick` [ui.py:938](../ui.py) раз в ~1 c проверять `is_offscreen(...)` → если да, вернуть в `anchor_to_xy(...)`.
- Рост от центра: в `_tick` блок «Keep the LEFT edge fixed» [ui.py:966](../ui.py) — для центральных якорей держать фиксированным центр, не левый край.
- Сателлиты `ControlBubble`/`CancelUndoToast`/`PasteFallbackBubble` `_position()` — заменить ручную математику на `widget_position.dock_beside/dock_above` (единый flip-стиль).

### C. Settings → `ui.py` (SettingsWindow)
- Звук — **готовый блок**, вставить одной строкой:
  ```python
  from sound_settings_panel import SoundSettings
  SoundSettings(parent, player=app._snd,
                get_cfg=lambda: app.config.sounds,
                on_change=lambda: save_config(app.config)).pack(fill="x", padx=12, pady=8)
  ```
  Внутри — тумблер, громкость, пресет и по строке на событие (5 вариантов + ▶). Сам пишет в `config.sounds` и зовёт `app._snd.update/ set_variant`.
- (Опц.) выпадашка положения пилюли (`widget_position` зоны) — если захотим UI вместо drag-snap.

Ничего из этого не пересекается по строкам с активной работой другого агента, кроме точечных вставок в перечисленные функции — применяется быстро и линейно.

---

## Источники
- [superwhisper — changelog](https://superwhisper.com/changelog) (sound effects start/end/pre-stop/empty-result, themes; re-center if not within bounds; mini snap points; placeholder wave avoid popping).
- [superwhisper — recording window](https://superwhisper.com/docs/get-started/interface-rec-window) (states, цветной dot, hover-to-reveal stop).
- [Wispr Flow docs](https://docs.wisprflow.ai/) · [PillFloat (бар locked bottom-center)](https://www.producthunt.com/products/pillfloat).
- [Apple — Dictation on Mac](https://support.apple.com/guide/mac-help/use-dictation-mh40584/mac) (ready-тон + «whiz»).
- Earcon design: [BeepBank-500 (arXiv)](https://arxiv.org/pdf/2509.17277) (частоты 350–1000 Гц, длит. 100/250/500 мс, ADSR) · [Earcon, the new icon](https://yujdesigns.medium.com/earcon-the-new-icon-3ce28c6b9d02) (мало звуков, консистентность).
- [Floating UI — concepts](https://floating-ui.com/docs/tutorial) (flip/shift/autoUpdate против дёрганья и коллизий).
- Ховер/состояния ✕/✓ (часть D): [UXPin — Button States](https://www.uxpin.com/studio/blog/button-states/) · [Mockplus — Button State Design](https://www.mockplus.com/blog/post/button-state-design) · [Carbon — Button](https://v10.carbondesignsystem.com/components/button/usage/) (icon-кнопки ring/fill; primary vs destructive).
- Мелкие цели: [NN/g — Fitts's Law](https://www.nngroup.com/articles/fitts-law/) · [Laws of UX — Fitts's Law](https://lawsofux.com/fittss-law/) · [Designing better target sizes](https://ishadeed.com/article/target-size/).
- [Saffer — Microinteractions](https://medium.com/@productandrew/microinteractions-dan-saffer-2013-ed12086b1ac9) (trigger·rules·feedback·loops). Опц.: [Hold-to-confirm](https://medium.com/@tomj.pro/why-holding-buttons-is-superior-to-confirmation-dialogs-in-ux-design-69790ff30e06).
- Пилюля: [Wispr Flow pill (long-press+drag select)](https://docs.wisprflow.ai/) · [110 UI micro-interaction examples](https://freefrontend.com/ui-micro-interaction/).
- Связано: концепты [21_wake_word](21_wake_word.md) (beep), [33_paste_fallback_recovery](33_paste_fallback_recovery.md) (плашка результата).
