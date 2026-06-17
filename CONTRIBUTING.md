# Вклад в Talker

Спасибо за интерес! Talker — фоновое Windows-приложение голосового ввода,
полностью локальное. PR и issue приветствуются.

## Запуск из исходников

```bash
git clone https://github.com/cutandship/talker
cd talker
pip install -r requirements.txt
pythonw main.py          # тихо (без консоли)
# или: python main.py    # с консолью — видно ошибки старта
```

Нужны: Windows 10/11, Python 3.11+, микрофон. При первом запуске скачается
модель распознавания (~250 МБ).

## Тесты

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

CI гоняет тесты на `windows-latest` для каждого push/PR (см.
`.github/workflows/test.yml`). PR должен оставлять тесты зелёными.

## Стиль

- Код и UI — в духе окружающего кода: те же отступы, именование, плотность
  комментариев. Без переписывания не по теме PR.
- UI живёт в пакете `ui/`, не в монолите.
- Не коммить личные/секретные файлы: `config.toml`, `*.log`, `history.json`,
  `phone_connect/` (приватный ключ), модели и `*.whl` — они в `.gitignore`.

## Перед PR

1. Опиши **что** и **зачем** (проблема → решение), а не построчный дифф.
2. Прогони `pytest tests/`.
3. Одна логическая правка — один PR; не мешай рефакторинг с фичей.
