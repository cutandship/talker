# Changelog

Все заметные изменения проекта. Формат — по мотивам
[Keep a Changelog](https://keepachangelog.com/ru/1.1.0/);
проект придерживается [семантического версионирования](https://semver.org/lang/ru/).

## [Unreleased]

### Изменено
- Подготовка к публичному релизу: README (RU + EN), бейджи, CONTRIBUTING,
  шаблоны issue/PR, `config.example.toml`.
- `web_ui` / документация переведены на бренд-контакты Cut & Ship.
- UI вынесен в пакет `ui/` (settings/history/flowbar/bubbles/common/url_window);
  монолитный `ui.py` удалён.
- Настройки: человечные формулировки, упрощён hands-free stop.

### Исправлено
- Трей залипал на «Загрузка модели…» при тёплом кэше GigaAM (гонка создания
  иконки трея).
- Залипание control-bubble и перехват AltGr-хоткея в web-UI.

### Удалено
- Фичи LLM-эры: роли, режимы, command-mode, облачная очистка — ради полной
  локальности.
