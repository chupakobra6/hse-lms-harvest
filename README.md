# hse-lms-harvest

Локальный сборщик страниц HSE Smart LMS. Он открывает отдельный браузерный профиль, умеет автоматически обновлять вход через `.env` и сохраняет видимый текст страниц, ссылки, кнопки, вероятные вложения, скачанные файлы и `manifest.json` для дальнейшей работы агента.

Пароли в код не передаются. `.env`, профиль браузера и дампы исключены из git.

## Установка

```bash
cd hse-lms-harvest
uv sync --extra dev
```

Стандартные команды проекта также доступны через `make`, как в соседних tooling-репозиториях:

```bash
make help
make setup
make test
make lint
make check
```

По умолчанию используется установленный Google Chrome через Playwright. Если нужно именно bundled Chromium:

```bash
uv run python -m playwright install chromium
```

и дальше добавляй `--browser-channel chromium`.

## Секреты

```bash
uv run hse-lms-harvest credentials set \
  --username "student@edu.hse.ru" \
  --env-file ".env" \
  --password-stdin
```

`.env` создаётся в корне проекта с правами `0600` и не попадает в git.

## Сбор курса

```bash
uv run hse-lms-harvest harvest \
  --url "https://edu.hse.ru/my/courses.php" \
  --profile ".browser-profile" \
  --out "dumps" \
  --course-title "Проектный семинар" \
  --max-pages 260 \
  --download-files \
  --ensure-login \
  --auto-login \
  --headless
```

Аудио, видео, личные отправленные ответы из `assignsubmission_file/submission_files` и служебные файлы конференций вроде `chat.txt`, `playback.m3u`, `audio_only.*` и `zoom_*` не скачиваются по умолчанию. Личные submission-ссылки, оценки, календарь, сообщения и вторичные LMS detail/report links (`quiz/review.php`, `h5pactivity/report.php`, `glossary/showentry.php`) убираются из capture text и `links`, чтобы агент видел материалы курса, а не приватный или навигационный шум. Для редкого полного архива медиа добавляй `--download-media`.
Учебные вложения с LMS file-server скачиваются при `--download-files`; если нужен режим "только записать ссылки, но не скачивать эти вложения", добавляй `--skip-lms-file-server`.
Один скачиваемый файл ограничен 80 MiB; если реально нужен крупный PDF/архив, подними лимит через `--max-file-mb 200` или отключи его через `--max-file-mb 0`.
Перед скачиванием файла сборщик делает быстрый `HEAD`-запрос: заранее отсекает видео/аудио, HTML-страницы и слишком большие файлы, не загружая тело ответа. Таймаут проверки короткий по умолчанию (`1500` ms) и задаётся через `--file-head-timeout-ms`; для максимально быстрого, но менее проверенного прохода можно поставить `--file-head-timeout-ms 0`.
Повторные запуски используют persistent-кеш вложений `dumps/_file-cache`: если `ETag`, `Last-Modified`, размер или тип файла совпали, файл берётся из кеша и кладётся в новый дамп через hard link/copy без повторного скачивания. Отключить можно через `--no-file-cache`, перенести кеш — через `--file-cache-dir`. Для повторного локального прогона по уже проверенному дампу можно добавить `--trust-file-cache`, тогда cached URLs берутся из кеша без нового `HEAD`.
Страницы тоже можно переиспользовать из предыдущего `manifest.json`: дефолтный `--page-cache validate` пропускает страницу только если HTTP-валидаторы страницы совпали. Сначала пробуется `HEAD`, а если LMS не отвечает на него, делается лёгкий GET metadata-probe без браузерного рендера. `--page-cache trust` переиспользует страницу по URL без сетевой проверки и подходит только для быстрых локальных повторов, когда ты сознательно принимаешь риск пропустить свежие изменения. Конкретный старый дамп можно указать через `--reuse-dump path/to/manifest.json`.
Проверка и скачивание вложений идут параллельно с лимитом `--download-concurrency 6`; если LMS начнёт отвечать нестабильно, уменьши до `2`, если сеть нормальная и файлов много — можно поднять до `10`. Таймаут самого скачивания задаётся через `--file-download-timeout-ms`.
Страницы открываются до `domcontentloaded`; общее ожидание фоновой тишины LMS по умолчанию отключено ради скорости. Для корневых страниц курса отдельно действует короткое `--course-network-idle-timeout-ms 1000`, потому что оглавление курса часто догружается лениво. Если на конкретном вложенном модуле контент тоже догружается поздно, добавь общий флаг `--network-idle-timeout-ms 1000`.
Во время чтения страниц сборщик по умолчанию блокирует тяжёлые page assets: картинки, видео/аудио и шрифты. Если нужны визуально полные debug-скриншоты, добавляй `--load-page-assets`.
Скриншоты по умолчанию сохраняются только для ключевых мест входа/выбора курса и ошибок, в JPEG, с лимитом по количеству. Полный скриншот каждой страницы включается только через `--screenshot-mode every-page`.
Ошибки пишутся в структурированную диагностику: `debug/events.jsonl`, `debug/errors.md`, `debug/errors.json` и отдельные папки `debug/errors/<id>/` со скриншотом и компактным `page-state.json`. По умолчанию HTML страницы не сохраняется; если нужно разобрать сложный UI-баг, включай `--debug-dump-mode verbose`. Если диагностика вообще не нужна, можно поставить `--debug-dump-mode off` и `--screenshot-mode off`.

Результат будет в новой папке `dumps/<host>-YYYYMMDD-HHMMSS/`:

- `manifest.json` — машинно читаемый источник правды;
- `navigation.md` — компактная карта локальных `pages/*.md`, `pages/*.json` и скачанных файлов;
- `navigation.json` — машинно читаемая версия карты без LMS URL;
- `summary.md` — короткая сводка без URL страниц и внутренних индексов;
- `pages/*.md` — компактный текст страницы без повторяющихся навигационных строк, служебных URL, id/hash и action-ссылок;
- `pages/*.json` — полная структура страницы: текст, ссылки, кнопки, ошибки;
- `files/` — скачанные вложения, если включен `--download-files`.
- `debug/screenshots/` — только ключевые/ошибочные скриншоты в JPEG; не снимок каждой страницы.
- `debug/errors.md` — короткий индекс ошибок с путями к диагностическим артефактам.

`navigation.md` удобно давать агенту первым: он показывает дерево захваченных страниц, локальные пути к Markdown/JSON каждой страницы и индекс скачанных вложений. Текст в `pages/*.md` сохраняет смысл гиперссылок прямо в строке, но без URL-таргетов: `[Презентация](https://...)` превращается в `Презентация`. Разделы `Links`, `Buttons` и `Downloaded files` в Markdown тоже пишутся в компактном виде без служебных адресов и хешей; полный список ссылок, кнопок, источников и checksum остаётся в `pages/*.json` и `manifest.json`.
Диагностику из `debug/` обычно не нужно передавать агенту целиком. Начинай с `debug/errors.md`, а конкретный `debug/errors/<id>/page-state.json` или скриншот открывай только для нужной ошибки.

## Миграция существующих дампов

Если поменялся только формат Markdown/navigation/manifest, не нужно заново ходить в LMS. Пересобери локальные файлы из уже сохранённого `manifest.json`:

```bash
uv run hse-lms-harvest migrate --out dumps/current-subjects
```

Команда рекурсивно обновляет все найденные `manifest.json` под `--out`. Для одиночного корня с несколькими историческими дампами можно добавить `--latest-only`, чтобы обновить только самый свежий manifest. Миграция не скачивает файлы и не открывает страницы; она только пересчитывает `pages/*.md`, `pages/*.json`, `summary.md`, `navigation.*` и служебные поля manifest.

## Детали заданий

Smart LMS иногда прячет нужную информацию за ссылками вроде "Добавить ответ на задание". Сборщик по умолчанию открывает такие страницы для чтения, потому что без этого часть условий задания может не попасть в дамп.

Для аккуратного чтения таких страниц:

```bash
uv run hse-lms-harvest harvest \
  --url "https://edu.hse.ru/course/view.php?id=..." \
  --profile ".browser-profile" \
  --download-files \
  --ensure-login
```

Сборщик не нажимает `Сохранить`, `Отправить`, `Удалить` и похожие кнопки. Флаг `--allow-state-changes` разрешает только явные completion-toggle элементы вроде `Отметить как выполнено`; страницы `Добавить ответ` читаются без отправки формы. Если нужно отключить такое открытие, добавь `--skip-action-pages`.

Если Smart LMS не сохраняет сессию после закрытия браузера, запускай сразу `harvest --ensure-login`: ты логинишься один раз, а сбор начинается в том же браузерном контексте без повторного входа.

## Очистка

```bash
uv run hse-lms-harvest cleanup --all --out dumps --profile .browser-profile
```

Команда удаляет debug-скриншоты, скачанные аудио/видео, служебные conference artifacts и rebuildable browser/cache/component artifacts Chrome, но не удаляет `.env`, cookies и полезные документы/PDF/DOCX/XLSX.
Кеш вложений намеренно не входит в `--all`, потому что ускоряет повторные сборы. Если нужно полностью освободить место от него:

```bash
uv run hse-lms-harvest cleanup --file-cache
```

## Проверка проекта

```bash
make check
make doctor
make smoke
```

`make smoke` использует `about:blank` и временные директории в `/tmp`, поэтому не ходит в LMS и не требует авторизации.

## Структура репозитория

- `src/hse_lms_harvest/cli_args.py` — argparse-схема команд и defaults.
- `src/hse_lms_harvest/cli.py` — CLI entrypoint, команды и Playwright orchestration.
- `src/hse_lms_harvest/capture.py` — чтение страниц, inline-ссылки, read-only action/detail pages.
- `src/hse_lms_harvest/classify.py` — классификация ссылок, файлов, медиа, unsafe navigation и личных submission-файлов.
- `src/hse_lms_harvest/downloads.py` — скачивание вложений, HEAD-проверки, лимиты, file cache.
- `src/hse_lms_harvest/page_cache.py` — reuse страниц из предыдущих manifest через `--page-cache`.
- `src/hse_lms_harvest/manifest.py` и `render.py` — manifest, миграции и компактный Markdown/navigation слой.
- `src/hse_lms_harvest/debug.py` — структурированные события, ошибки и скриншоты.
- `tests/` — unit-тесты публичных контрактов без живых LMS-запросов.
- `dumps/`, `.browser-profile/`, `.env`, `.venv/` — локальные приватные артефакты, исключены из git.

## Безопасность

- Не коммить `.env`, `.browser-profile`, `dumps`, cookies, скриншоты личного кабинета и логи с приватными URL.
- Не запускай сбор с `--allow-state-changes`, если не хочешь менять состояние курса.
- Для передачи агенту обычно начинай с `navigation.md`, затем при необходимости давай `summary.md`, нужные `pages/*.md` и скачанные учебные файлы. Полная структура остаётся в `manifest.json` и `pages/*.json`.
