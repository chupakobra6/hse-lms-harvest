# AGENTS.md

## Назначение проекта
- `hse-lms-harvest` — локальный `uv`/Python-инструмент для легитимного сбора страниц HSE Smart LMS из браузерного профиля пользователя.
- Проект читает доступные пользователю страницы, раскрывает read-only элементы, сохраняет текст, ссылки, кнопки, структуру страниц, учебные вложения и краткий Markdown для дальнейшей работы агента.
- Это не security-инструмент. Не добавляй обход авторизации, CAPTCHA, CSRF, Cloudflare, rate limits, платного доступа или чужих аккаунтов.
- Главная инженерная цель: корректное извлечение учебного контекста при минимальной нагрузке на LMS и без изменения состояния курса.

## Обязательные инварианты
- Поведение по умолчанию должно быть консервативным: читать, раскрывать, скачивать учебные файлы, но не отправлять формы и не менять состояние курса.
- `pluginfile.php` и `/webservice/pluginfile.php` — обычные Moodle file-server URL для учебных вложений. Не считай их вредными и не удаляй поддержку.
- При `--download-files` учебные same-site вложения скачиваются, включая file-server URL. `--skip-lms-file-server` — только явный opt-out: записать ссылки, но не скачивать эти вложения.
- Личные отправленные ответы из `assignsubmission_file/submission_files` не скачиваются и не попадают в `links`/capture text: это приватный шум, а не учебные материалы курса для агентного контекста.
- Оценки, календарь, сообщения и вторичные LMS detail/report links (`mod/glossary/showentry.php`, `mod/quiz/review.php`, `mod/h5pactivity/report.php`) не должны попадать в `links`/capture text и crawl-очередь.
- Аудио/видео и Moodle conference artifacts (`chat.txt`, `playback.m3u`, `audio_only*`, `zoom_*`) не скачиваются по умолчанию. Для полного медиа-архива есть `--download-media`.
- Страницы `Добавить ответ на задание` открываются по умолчанию как read-only action/detail pages, потому что там может появляться текст задания. Отключение только через `--skip-action-pages`.
- Никогда не нажимай `Сохранить`, `Отправить`, `Удалить`, `Редактировать ответ` и похожие state-changing controls в обычном режиме.
- `--allow-state-changes` может разрешать только явные completion toggles вроде `Отметить как выполнено`; save/submit/delete всё равно запрещены.
- Если меняешь правила кликов, очереди ссылок или классификации ссылок, сначала проверь, что прямой сбор курса не уходит в другие курсы, профиль, календарь, сообщения или оценки.
- Не ставь в crawl-очередь вторичные report/detail pages вроде `mod/glossary/showentry.php`, `mod/quiz/review.php`, `mod/h5pactivity/report.php`: они быстро раздувают историю/тесты и обычно не нужны для учебного контекста.
- Не делай продакшен-вызовы в тестах. Для тестов используй локальные фикстуры, `about:blank`, временные директории и чистые unit-тесты.

## Формат дампа
- `manifest.json` — машинно читаемый источник правды после capture-level фильтров. В нём остаются полные URL полезных ссылок, кнопки, источники файлов, checksum и ошибки.
- `pages/*.json` — полная структура отдельной страницы после capture-level фильтров. Не урезай эти JSON ради экономии контекста.
- `navigation.md` — первый файл для агента: дерево захваченных страниц, локальные ссылки на `pages/*.md`, `pages/*.json` и индекс скачанных вложений.
- `navigation.json` — машинно читаемая версия навигации без LMS URL; полные URL всё равно брать из `manifest.json`/`pages/*.json`.
- `summary.md` и `pages/*.md` — компактный слой для человека/агента. Он должен экономить внимание и контекст.
- `debug/events.jsonl` — структурированный журнал событий. Используй его для расследования, но не подсовывай целиком как основной контекст.
- `debug/errors.md` — короткий индекс ошибок; начинать отладку с него.
- `debug/errors.json` и `debug/errors/<id>/` — полные error bundles: exception, redacted URL, details, screenshot path, `page-state.json`, а при `--debug-dump-mode verbose` ещё `page.html`.
- Markdown не должен дублировать служебные URL, `id`, `sesskey`, `pluginfile.php`, `sha256`, `source:`, action URL кнопок и навигационный шум.
- Inline-гиперссылки в Markdown сохраняют смысл как текст: `[Презентация](https://...)` рендерится как `Презентация`. Полный URL остаётся в JSON.
- В `Links` для Markdown показывай только смысловые уникальные labels. Повторяющиеся ссылки, ссылки уже видимые в тексте и UI-навигацию подавляй.
- В `Buttons` для Markdown оставляй только кнопки, важные для понимания контекста, например `Добавить ответ на задание`; не выводи action URL.
- В `Downloaded files` для Markdown показывай только локальные пути `files/...`; не выводи `sha256` и source URL.
- Дедуплицируй повторные строки внутри одной Markdown-страницы после компактирования текста.

## Производительность и нагрузка
- Не загружай тяжёлые page assets по умолчанию: изображения, шрифты, аудио и видео блокируются, если не включён `--load-page-assets`.
- Для файлов сначала делай быстрый metadata check (`HEAD`) с коротким таймаутом, если он не отключён явно через `--file-head-timeout-ms 0`.
- Уважай лимит размера `--max-file-mb`; не обходи его неявно.
- Повторные запуски должны использовать persistent-кеш вложений `dumps/_file-cache`, если не передан `--no-file-cache`.
- `--trust-file-cache` предназначен для быстрых повторных локальных прогонов по уже проверенному кешу; не делай его неявным дефолтом.
- Для страниц используй `--page-cache validate` как безопасный дефолт: переиспользовать страницу можно только при совпавших HTTP-валидаторах. Если `HEAD` на LMS не работает, допустим лёгкий GET metadata-probe без браузерного рендера. `--page-cache trust` — явный быстрый режим для локального повтора, где пользователь принимает риск пропустить изменения.
- Если меняется только формат дампа/рендер Markdown/navigation, сначала используй `hse-lms-harvest migrate`, а не новый live harvest.
- Сохраняй ограниченную параллельность скачивания через `--download-concurrency`; не повышай дефолт без доказанной необходимости.
- Общее ожидание `networkidle` по умолчанию отключено ради скорости; корневые `/course/view.php` ждут короткий `--course-network-idle-timeout-ms`, чтобы не потерять лениво загружаемое оглавление. Не добавляй долгие sleep без теста и причины.

## Приватность и секреты
- Никогда не коммить и не включай в ответы содержимое `.env`, `.browser-profile/`, cookies, личные URL с токенами, private screenshots, `dumps/`, `harvest.log`.
- Не печатай пароль, cookies, `sesskey`, auth headers или приватные query tokens.
- Если нужно показать URL в диагностике, пропускай его через существующие privacy/redaction helpers.
- `dumps/current-subjects` и другие дампы — локальные приватные артефакты, не тестовые fixtures и не часть публикуемого результата.
- Диагностические артефакты из `debug/` считаются приватными: они могут содержать видимый текст LMS и скриншоты.

## Карта кода
- `src/hse_lms_harvest/cli_args.py` — argparse-схема команд, defaults и help text.
- `src/hse_lms_harvest/cli.py` — CLI entrypoint, команды, Playwright orchestration и очередь страниц.
- `src/hse_lms_harvest/classify.py` — классификация ссылок, файлов, медиа, Moodle artifacts и потенциально опасной навигации.
- `src/hse_lms_harvest/render.py` — запись `manifest.json`, `navigation.*`, `pages/*.json`, компактного `pages/*.md` и `summary.md`.
- `src/hse_lms_harvest/manifest.py` — чтение старых manifest, миграция дампов, page fingerprints и page-cache reuse helpers.
- `src/hse_lms_harvest/page_cache.py` — page-cache index, validation probes and reused page materialization.
- `src/hse_lms_harvest/file_cache.py` — persistent file cache, metadata, hard link/copy reuse.
- `src/hse_lms_harvest/privacy.py` — redaction/safe URL helpers.
- `src/hse_lms_harvest/debug.py` — `RunLogger`, screenshots, structured diagnostics, error bundles.
- `src/hse_lms_harvest/text.py` — нормализация текста, slug, подавление повторяющихся строк.
- `src/hse_lms_harvest/model.py` — dataclass-модель `Link`, `Button`, `PageCapture`.
- `src/hse_lms_harvest/credentials.py`, `auth.py` — хранение credentials и login flow. Не расширяй их в сторону обхода авторизации.
- `src/hse_lms_harvest/course.py` — course matching.
- `tests/` — unit-тесты на публичные контракты. При изменении поведения обновляй или добавляй тест рядом с соответствующим модулем.

## Рабочий процесс
- Перед изменениями прочитай ближайший код и существующие тесты; следуй текущим паттернам проекта.
- Держи дифф маленьким. Не делай широких рефакторов, переездов файлов и новых зависимостей без явной причины.
- Если меняешь публичное поведение CLI, формат дампа, defaults или safety semantics, обнови `README.md` и этот файл при необходимости.
- Если меняешь `render.py`, проверь, что JSON остаётся полным, Markdown остаётся компактным, а `navigation.md` ведёт на существующие локальные файлы.
- Если меняешь `classify.py` или правила скачивания, добавь тесты на учебные вложения, Moodle file-server URL, медиа/artifacts и unsafe navigation.
- Если меняешь browser automation, добавь тест на intent/guard rail там, где можно без живой LMS, и сделай smoke test с `about:blank`.
- Если добавляешь новый `except` или новый путь ошибки, записывай его через `DiagnosticRecorder.error(...)` или `DiagnosticRecorder.warning(...)`, чтобы он попал в `debug/events.jsonl` и, для ошибок, в `debug/errors.*`.
- Не используй реальные LMS-запросы в автоматических тестах.

## Команды
- Список стандартных команд: `make help`
- Установка/обновление зависимостей: `make setup` или `uv sync --extra dev`
- Помощь CLI: `uv run hse-lms-harvest --help`
- Фокусный тест: `uv run pytest tests/test_render.py` или ближайший файл из `tests/`.
- Все тесты: `make test` или `uv run pytest`
- Lint: `make lint` или `uv run ruff check .`
- Проверка форматирования: `uv run ruff format --check .`
- Автоформатирование: `uv run ruff format .`
- Полная локальная проверка: `make check`
- Проверка CLI entrypoints: `make doctor`
- Безопасный smoke test без LMS:
  `uv run hse-lms-harvest harvest --url about:blank --profile /tmp/hse-lms-harvest-profile --out /tmp/hse-lms-harvest-smoke --max-pages 1 --headless`
- Компактная диагностика включена по умолчанию: `--debug-dump-mode on-error`. Для полного HTML при сложном UI-баге используй `--debug-dump-mode verbose`; для минимального дампа `--debug-dump-mode off --screenshot-mode off`.
- Очистка rebuildable/heavy artifacts:
  `uv run hse-lms-harvest cleanup --all --out dumps --profile .browser-profile`
- Очистка file cache только по явному запросу:
  `uv run hse-lms-harvest cleanup --file-cache`
- Миграция текущего набора предметов без сети:
  `uv run hse-lms-harvest migrate --out dumps/current-subjects`

## Проверки по типу изменения
- Любое изменение кода: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
- Изменение Markdown/summary рендера: дополнительно проверь, что в `pages/*.md` нет `https?://`, `sha256:`, `source:`, `pluginfile.php`, `Source:`, `Title:` и action URL.
- Изменение navigation рендера: проверь, что `navigation.md` не содержит LMS URL/hash/source, но содержит локальные `pages/*.md`, `pages/*.json` и `files/...`.
- Изменение JSON/manifest контракта: проверь readback тестом, что полные ссылки и file metadata не потерялись.
- Изменение file download/cache: запускай `tests/test_file_cache.py`, релевантные `tests/test_cli.py`, и проверяй cached/materialized file path behavior.
- Изменение page-cache/migration: запускай `tests/test_manifest.py` и smoke на локальном/старом manifest без LMS.
- Изменение cleanup: тестируй, что полезные документы/PDF/DOCX/XLSX не удаляются, а cache не чистится через `--all`.
- Изменение login/credentials: проверь, что секреты не попадают в stdout, Markdown, JSON tests или docs.
- Изменение диагностики: проверь, что `debug/errors.md`, `debug/errors.json`, `debug/events.jsonl` создаются, URL с `sesskey` редактируются, а `debug/` не попадает в компактный Markdown/navigation.

## Быстрый аудит Markdown-шума
- Для локального приватного дампа можно проверить:
  `rg -n "https?://|sha256:|source:|pluginfile\\.php|^- Source:|^- Title:|^\\s+URL:" dumps/current-subjects -g '*.md'`
- Ожидаемый результат после компактного рендера — пустой вывод.
- Не переносить приватные дампы в тестовые fixtures и не коммитить результаты аудита.

## Документация для пользователя
- README должен объяснять реальные defaults: read-only сбор, `--download-files`, `--skip-lms-file-server`, file cache, media skip, navigation files, compact Markdown и безопасность форм.
- Не обещай, что Markdown содержит полные кликабельные ссылки. Полные ссылки находятся в JSON.
- Не называй `pluginfile.php` подозрительным файлом; это нормальный путь Moodle для учебных вложений.
