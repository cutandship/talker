# Talker — голосовой ввод для Windows

[![tests](https://github.com/cutandship/talker/actions/workflows/test.yml/badge.svg)](https://github.com/cutandship/talker/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6.svg)

🇷🇺 Русский · [🇬🇧 English](README.en.md)

**Говоришь — текст появляется там, где стоит курсор.** Полностью офлайн:
ни один звук и ни одно слово не покидают твой компьютер. Русский — первым
классом: распознаёт [GigaAM v3](https://github.com/salute-developers/GigaAM)
(лучшая открытая модель для русской речи), любой другой язык — Whisper.

![Демо Talker](assets/demo.gif)

## Скачать

### ⬇️ [Установить Talker — TalkerSetup.exe (292 МБ)](https://github.com/cutandship/talker/releases/latest)

Офлайн-инсталлятор для Windows 10/11: модель распознавания, рантайм и все
зависимости уже внутри — работает сразу, без интернета. Python ставить не нужно.

## Возможности

- **Push-to-talk** — зажми `Right Alt`, говори, отпусти. Текст вставится в
  активное окно (Telegram, Word, браузер, IDE — куда угодно).
- **Диктовка без рук** — `Ctrl+Alt+Space` или wake-word «Hey Jarvis…
  стоп-стоп». Медиа-гард не даёт фильму в колонках печатать за тебя.
- **Умная пост-обработка без всякого ИИ-облака**: слова-паразиты («ну»,
  «э-э») вырезаются, «двадцать пять процентов» → «25 %», голосовые команды
  форматирования («пункт один… пункт два», «новый абзац», «тире») → настоящие
  списки и абзацы, словарь имён и терминов, замены («клод» → «Claude»).
- **Плавающая капсула** с живой волной голоса и кнопками ✕/✓ — не крадёт
  фокус у окна, в которое диктуешь.
- **История** с мгновенным поиском, экспорт в TXT/SRT/VTT/JSON.
- **Транскрибация файлов и YouTube** — перетащи mp3/mp4 или вставь ссылку.
- **Тихий режим** — для шёпота (отдельное усиление и пороги).
- **Локальный HTTP API** — для Raycast/vim/скриптов (127.0.0.1, токен).
- Настройки и история — лёгкие веб-окна (один HTML-файл), без браузерных
  вкладок, с тёмной и светлой темой.

## Установка

Нужны: Windows 10/11, Python 3.11+, микрофон.

```bash
git clone https://github.com/cutandship/talker
cd talker
pip install -r requirements.txt
pythonw main.py
```

При первом запуске скачается модель распознавания (~250 MB для GigaAM,
прогресс виден в трее). Дальше — полностью офлайн.

Появится капсула на экране и иконка в трее. **Зажми `Right Alt` и скажи
что-нибудь** — текст появится там, где курсор.

## Горячие клавиши

| Действие | Клавиша |
|---|---|
| Диктовка (зажать и говорить) | `Right Alt` (меняется в настройках) |
| Диктовка без рук (вкл/выкл) | `Ctrl+Alt+Space` |
| Старт/стоп голосом | «Hey Jarvis» … «стоп-стоп» (опция) |
| Настройки / История | иконка в трее, правый клик по капсуле |

## Приватность

Распознавание, история и настройки живут только на твоей машине. Нет
телеметрии, нет облака, нет аккаунтов. Лог не содержит текста диктовок.

## Тесты

```bash
pip install -r requirements-dev.txt
pytest tests/
```

## Что дальше (Talker+)

Бесплатный Talker останется бесплатным. В платной версии планируются вещи,
которым нужны серверы и синхронизация: телефон как микрофон через интернет,
митинг-режим (кто что сказал + саммари), синк словаря между устройствами,
работа без установки Python. Онлайн-версия в браузере и расширенный
**Talker+** — на [cutandship.dev](https://cutandship.dev). Новости:
[t.me/cut_and_ship](https://t.me/cut_and_ship).

## Автор

**Cut & Ship** — [cutandship.dev](https://cutandship.dev) ·
[Telegram](https://t.me/cut_and_ship) · <cutandship@proton.me>

Делаю кастомные голосовые интеграции под заказ: голосовой ввод в вашем
приложении, CRM или внутреннем инструменте — на вашей инфраструктуре, без
утечки данных наружу. Пишите.

## Лицензия

[MIT](LICENSE). Модели: [GigaAM](https://github.com/salute-developers/GigaAM)
(MIT), [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(MIT). Шрифт [Inter](https://rsms.me/inter/) — SIL OFL 1.1. Полный список
сторонних лицензий — [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
