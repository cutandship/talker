# Патчи openWakeWord под Windows

Это **наши изменённые версии** трёх файлов из библиотеки openWakeWord.
Библиотека официально работает только на Linux; чтобы обучение пошло на
Windows, пришлось внести 6 правок. Они описаны в
[`../docs/РАЗБОР_ПОЛЁТОВ.md`](../docs/РАЗБОР_ПОЛЁТОВ.md) (баги #3-#6).

## Зачем эта папка

Сам клон `openWakeWord/` не в git (большой). Если переустановить библиотеку
заново из интернета — **все патчи слетят** и обучение снова сломается. Эти
копии — чтобы можно было восстановить.

## Что где патчено

| Файл | Что изменено |
|---|---|
| `data.py` | `trim_mmap`: закрытие memory-map перед удалением файла (WinError 32) |
| `utils.py` | `compute_features_from_generator`: закрытие write-mmap перед trim |
| `train.py` | `num_workers=0` (pickle на Windows) + tflite в try/except |

## Как применить (после свежей установки openWakeWord)

```powershell
# из папки wake_training, ПЕРЕЗАПИСАТЬ файлы библиотеки нашими:
copy openwakeword_patches\data.py  openWakeWord\openwakeword\data.py
copy openwakeword_patches\utils.py openWakeWord\openwakeword\utils.py
copy openwakeword_patches\train.py openWakeWord\openwakeword\train.py
```

> Версия openWakeWord, под которую сделаны патчи: **0.6.0**. Для другой версии
> правки могут не подойти 1-в-1 — тогда смотри РАЗБОР_ПОЛЁТОВ.md и вноси по
> смыслу (места помечены комментариями `# WINDOWS FIX`).
