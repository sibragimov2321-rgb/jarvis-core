# JARVIS Core

Персональный backend на Python/FastAPI: iPhone → HTTPS API → Google Workspace/Jules → короткий ответ.
Существующие Gem, документы и другие проекты не изменялись. Gemini UI не автоматизируется.

## Что реализовано

- Google OAuth Authorization Code + PKCE, одноразовый state, привязка к браузеру, offline refresh.
- Calendar: список, создание событий с уведомлением, удаление с подтверждением.
- Tasks: список, создание, срок по дате, удаление с подтверждением.
- Drive: поиск файлов по имени, пагинация; чтение JARVIS_MEMORY через официальный Google Docs API.
- Gmail: поиск, чтение, отправка и перемещение в корзину с подтверждением.
- Память: дополнение существующего Google Doc без удаления текста/форматирования; проверка requiredRevisionId;
  зашифрованная локальная история с текстом до изменения, добавлением и результатом API.
- Jules: источники, создание сессии после подтверждения, статус/outputs, activities с пагинацией.
  Всегда requirePlanApproval=true; план одобряется отдельно в интерфейсе Jules. Автоматическое создание PR выключено.
- Разговор/совет через необязательный отдельный Gemini API; память передаётся как контекст.
  Модель не получает инструментов и не может подтвердить или выполнить действие.
- Bearer-аутентификация всех бизнес-маршрутов, одноразовые подтверждения с TTL, UUID запросов, защита от дублей.
- Docker, Railway, VPS с Caddy/HTTPS, тесты и GitHub Actions.

## Границы готовности

Код и локальные тесты готовы. Реальные аккаунты не подключены, документы/почта/Calendar не изменены,
Jules-сессии не создавались, сайт не опубликован. Docker-сборка требует установленного Docker;
на машине разработки Docker отсутствовал. См. `VALIDATION.md`.

Это backend одного владельца с одним OAuth-аккаунтом и одной репликой. Для нескольких пользователей
нужны отдельные учётные записи и изоляция данных. Текущая БД — SQLite на постоянном диске.
Свободный русский язык ограничен явно описанными шаблонами; сложные команды передаются структурированно
через `action`. Неопределённое время не угадывается. Полный API-контракт доступен в `/docs`.

Keep, управление Drive-файлами, изменение произвольного форматирования Google Doc, исполнение shell,
merge и production-деплой из команды не реализованы. Память должна быть **нативным Google Doc**,
не PDF/DOCX. Если вкладок несколько, укажите `JARVIS_MEMORY_TAB_ID`. История охватывает записи через
этот backend; правки других редакторов остаются в нативной истории Google Docs.

## 1. Локальный запуск (Python 3.12+; проверено на 3.13)

Из каталога проекта в PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe scripts/init_env.py
# Откройте .env и заполните Google/Jules значения. Ключи JARVIS уже сгенерированы.
.\.venv\Scripts\python.exe -m uvicorn jarvis.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Linux/macOS: используйте `.venv/bin/python` вместо `.venv\Scripts\python.exe`.
Без Google credentials сервер запускается; `/status` покажет, что Google не подключён,
а действия Google вернут 503. Для /health аутентификация не нужна.

Откройте `http://localhost:8000/docs`, нажмите **Authorize**, вставьте значение `JARVIS_API_KEY`
(без слова Bearer). Все запросы вне Swagger передают `Authorization: Bearer <JARVIS_API_KEY>`.

## 2. Google: необходимые действия владельца

1. Создайте/выберите отдельный проект в Google Cloud Console.
2. Включите **Google Calendar API, Google Drive API, Google Docs API, Google Tasks API, Gmail API**.
3. Настройте OAuth consent screen. Для разработки External/Testing добавьте свой аккаунт в Test users;
   для внутреннего Workspace-приложения можно использовать Internal, если это доступно организации.
4. Создайте OAuth Client ID типа **Web application**. Запишите client ID/secret в `.env`.
5. Добавьте точный Authorized redirect URI: `http://localhost:8000/auth/google/callback`.
   После деплоя добавьте `https://ВАШ-ДОМЕН/auth/google/callback` и обновите `.env`/переменные сервиса.
6. В **том же браузере** откройте `/docs`, авторизуйтесь API-ключом, выполните
   `POST /auth/google/start`, затем откройте возвращённый `authorization_url` в новой вкладке.
   Не начинайте этот flow через curl/Shortcut: callback проверяет cookie браузера.
7. Войдите в свой Google-аккаунт и предоставьте все запрошенные разрешения. Callback подтвердит подключение.
8. Из ссылки `https://docs.google.com/document/d/FILE_ID/edit` возьмите FILE_ID → `JARVIS_MEMORY_ID`.
   При необходимости найдите документ через `GET /drive/files?name=JARVIS_MEMORY`; выбор по имени
   намеренно не автоматический, поскольку документы могут иметь одинаковые названия.
9. Проверьте `GET /memory` и `GET /status`. Google-аккаунт должен иметь право редактировать этот документ.

OAuth scopes (полный префикс: `https://www.googleapis.com/auth/`):

| Scope | Назначение |
|---|---|
| `calendar.events` | Читать/создавать/удалять события выбранного календаря |
| `drive.readonly` | Искать существующие файлы Drive; доступ шире одного документа |
| `documents` | Читать и дополнять существующий JARVIS_MEMORY; scope шире одного документа |
| `tasks` | Читать/создавать/удалять задачи |
| `gmail.modify` | Читать, отправлять и перемещать письма в корзину; без прямого окончательного удаления |

Подключение Workspace к Gemini/Codex не выдаёт OAuth-токены этому приложению: отдельное согласие необходимо.
`drive.readonly` и `gmail.modify` относятся к широким/restricted-разрешениям. Для публичного распространения
могут потребоваться verification и security assessment; для личного/внутреннего использования действуют
условия Google и политики вашего администратора. Не обходите предупреждения и ограничения администратора.

Для непрерывной работы учтите: у External-приложения в **Testing** refresh token обычно истекает через
7 дней для этих scopes. Настройте подходящий разрешённый publishing status/тип приложения; переход
в Production сам по себе не гарантирует вечный токен. Отзыв доступа и политики аккаунта требуют повторного OAuth.

## 3. Переменные окружения

| Переменная | Значение |
|---|---|
| `JARVIS_API_KEY` | Случайный секрет от 32 символов; init_env.py генерирует |
| `TOKEN_ENCRYPTION_KEY` | Fernet key; init_env.py генерирует. Сохраните резервную копию отдельно от БД |
| `GOOGLE_CLIENT_ID` | OAuth Web client ID |
| `GOOGLE_CLIENT_SECRET` | OAuth Web client secret |
| `GOOGLE_REDIRECT_URI` | Точный локальный/публичный callback URL |
| `JARVIS_MEMORY_ID` | ID существующего Google Doc |
| `JARVIS_MEMORY_TAB_ID` | Опционально: ID нужной вкладки Docs |
| `GOOGLE_CALENDAR_ID` | По умолчанию `primary`; можно отдельный календарь JARVIS |
| `GOOGLE_TASKLIST_ID` | По умолчанию `@default` |
| `TIMEZONE` | По умолчанию `Europe/Moscow` |
| `DATA_DIR` | Локально `./data`, в контейнере/Railway `/data` |
| `CONFIRMATION_TTL_SECONDS` | По умолчанию 300 |
| `JULES_API_KEY` | Отдельный API key из Jules Settings |
| `JULES_SOURCE` | Точное поле `name` из `GET /jules/sources`, например `sources/...` |
| `JULES_BRANCH` | Существующая ветка выбранного репозитория, по умолчанию `main` |
| `GEMINI_API_KEY` | Опционально: отдельный Gemini API key из Google AI Studio |
| `GEMINI_MODEL` | Опционально: ID модели, доступной вашему API key, без префикса `models/` |
| `DOMAIN` | Для VPS/Caddy: домен без https:// |

Подписка Gemini Pro и Gem не используются как API. При включении Gemini API содержимое выбранной
памяти (до 40 000 символов) передаётся в этот отдельный API; тарифы/квоты отдельные.
Диалог stateless: сохраняются только явные заметки/идеи, транскрипт разговора автоматически не записывается.

## 4. Команды и подтверждения

Каждое новое намерение получает новый UUID `request_id`. При сетевом повторе отправляйте **тот же UUID и тело**.

```json
{
  "request_id": "fc1916e2-cfc1-4427-8932-055391c2b8a3",
  "text": "завтра в 11 напомни проверить trading bot"
}
```

`POST /command` создаст 15-минутное событие в Calendar на завтра 11:00 в `TIMEZONE`, с popup в момент начала.
Для уведомления на iPhone включите уведомления Google Calendar и синхронизацию нужного календаря.
Google Tasks API хранит срок как дату и не создаёт точное push-напоминание по времени.

Другие текстовые шаблоны:

- `сегодня в 18:30 напомни проверить отчёт` (только будущее время)
- `послезавтра в 9 напомни встреча`
- `идея: добавить отчёт по торговым ботам`
- `заметка: предпочитаю короткие ответы`
- `задача: проверить логи`
- `программисту: добавить тесты в выбранный репозиторий`
- `календарь`, `задачи`, `почта`, `drive: JARVIS_MEMORY`
- произвольный вопрос → разговор/совет через отдельный Gemini API.

Структурированные примеры (для каждого вызова создайте свой UUID):

```json
{"request_id":"8b4f3646-5681-4a37-89a4-ad770c615e56","action":"tasks.create","title":"Проверить trading bot","due":"2027-01-10"}
```

```json
{"request_id":"f1e99d66-0383-4dc1-83e7-20d325d40ec8","action":"calendar.create","title":"Встреча","when":"2027-01-10T11:00:00+03:00"}
```

```json
{"request_id":"131960b9-1ebd-4909-b53e-ded44d25a05b","action":"gmail.send","to":"person@example.com","subject":"Проверка","body":"Точный текст письма"}
```

Письмо пока **не отправлено**: ответ `status=confirmation_required` содержит `preview`,
`confirmation_token`, `expires_at`. Покажите пользователю весь preview, затем только после его выбора:

```json
{"token":"ТОКЕН_ИЗ_ОТВЕТА","approve":true}
```

Отправьте это на `POST /confirm` с тем же Bearer. Для отмены `approve=false`.
Токен одноразовый и привязан к сохранённым параметрам/аккаунту/целям; подменять параметры в /confirm нельзя.
Фраза «подтверждаю» внутри команды не считается подтверждением. ИИ не должен иметь доступ к /confirm:
этот вызов выполняет интерфейс после ручного выбора владельца.

Для `calendar.delete`, `tasks.delete`, `gmail.trash` укажите `action` и `resource_id`;
все они требуют подтверждения. Drive delete и произвольные production-действия отсутствуют в allowlist.
Создание задачи Jules тоже требует подтверждения, поскольку запускает работу внешнего агента.

После таймаута состояние может быть `uncertain`: запрос мог выполниться у Google/Jules, но ответ потерялся.
Не отправляйте автоматически новый UUID. Проверьте `GET /requests/{request_id}` и целевой сервис.
Повторное исполнение заблокировано; у событий Calendar дополнительно детерминированный event ID.
Операции Google и локальная SQLite не образуют общую транзакцию — абсолютная гарантия exactly-once невозможна.

## 5. Память

`GET /memory` возвращает текст и `revision`. Для безопасного дополнения:

```json
{"request_id":"cfd8aa20-c5fc-435b-aa4a-7949f5c6a062","text":"Новое знание","expected_revision":"REVISION_ИЗ_GET_MEMORY"}
```

Отправьте на `POST /memory/append`. Конфликт до записи вернёт 409; отказ Google по устаревшей ревизии
между чтением и записью — 502 с кодом провайдера, и история пометит результат `unknown_or_failed`.
Перечитайте память и проверьте результат перед новым UUID. Автоматического затирания/слияния текста нет.
`GET /memory/history` возвращает последние 100 записей; полная история хранится в БД.
Восстановление старого текста вручную через Google Docs version history: API намеренно не перезаписывает документ.

## 6. Jules

Ваш GitHub уже подключён к Jules. В Jules Settings создайте API key, добавьте `JULES_API_KEY`,
получите `GET /jules/sources`, выберите разрешённый репозиторий и ветку в `.env`, перезапустите backend.
Команда `программисту: ...` → preview → ручное подтверждение → новая сессия с `requirePlanApproval=true`.
Вернувшийся `id` используйте в `GET /jules/sessions/{id}`: ответ содержит state и outputs/ссылки результата.
Ход работы: `GET /jules/sessions/{id}/activities`. Pending plan одобрите вручную на странице Jules
после просмотра. Backend не одобряет планы, не сливает PR и не публикует изменения.

## 7. Railway: подготовлено для 24/7

1. Создайте отдельный приватный GitHub-репозиторий и загрузите содержимое проекта **без .env и data/**.
2. В Railway создайте отдельный service из этого репозитория; `railway.json` выбирает Dockerfile.
3. Добавьте секреты из `.env` в защищённые Variables Railway (не в railway.json).
4. Подключите **Volume на `/data`**, установите `DATA_DIR=/data`. Нужна **одна replica**.
5. Railway монтирует Volume от root. По официальной инструкции добавьте Variable `RAILWAY_RUN_UID=0`
   для этого сервиса: иначе non-root образ может не записать SQLite. На VPS Docker использует UID 10001.
   Не используйте временный диск как замену Volume. /health проверяет доступность SQLite.
6. Включите публичный HTTPS-домен, укажите его callback в Google Cloud и GOOGLE_REDIRECT_URI.
7. Используйте тариф с постоянной работой, отключите режим сна/serverless, проверьте квоты и оплату.
8. Deploy, проверьте `/health`, затем OAuth через `/docs` уже на публичном домене.
9. Настройте резервное копирование Volume и внешний uptime-monitor. Healthcheck Railway проверяет запуск,
   а не гарантирует постоянный мониторинг работоспособности Google.

Эти шаги **не выполнены за вас**: публикация — отдельное production-действие, требующее подтверждения
владельца после просмотра проекта. Для этого проекта не создавались платные сервисы и не менялись другие сервисы.

## 8. VPS / Docker

Локальный Docker (после заполнения `.env`):

```sh
docker compose up -d --build
```

VPS: укажите DNS A/AAAA на сервер, откройте входящие 80/443, установите Docker Compose,
заполните DOMAIN и HTTPS callback в `.env`, затем:

```sh
docker compose --profile vps up -d --build
docker compose ps
```

Caddy получает TLS-сертификат; API-порт 8000 привязан только к loopback VPS. Снаружи используйте HTTPS/443.
Не включайте access logs с query string для OAuth callback (authorization code — чувствительные данные).
Compose использует постоянный named volume и restart unless-stopped. Обновления пакетов/образов и мониторинг
остаются задачей эксплуатации. Перед обновлением сохраните БД и ключ шифрования, затем пересоберите контейнер.

Секреты конфигурации только в `.env`/защищённых переменных хостинга. OAuth выдаёт динамические токены:
они сохраняются **зашифрованно** в `/data/jarvis.db`, а не в исходниках и не в открытом token.json.
Там же шифруются команды, подтверждения и история памяти. Бэкап делайте через SQLite backup API
или средствами согласованных volume snapshots; не копируйте один jarvis.db во время записи, игнорируя WAL.
БД и TOKEN_ENCRYPTION_KEY нужно сохранять вместе, но в разных защищённых местах.
API key в Shortcut даёт полный доступ владельца: не публикуйте заполненный Shortcut.

## 9. iPhone Shortcut

Подробно: [docs/IPHONE.md](docs/IPHONE.md). Схема: «Диктовать текст» → UUID → POST /command →
проверка status → при необходимости показать preview и спросить подтверждение → POST /confirm →
«Произнести текст» поля reply. Работает через Siri/Action Button/иконку на экране.

## Тесты

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check jarvis tests scripts
```

Официальные источники: [Google OAuth](https://developers.google.com/identity/protocols/oauth2/web-server),
[сроки refresh token](https://developers.google.com/identity/protocols/oauth2),
[Docs write control](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate),
[Tasks](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks),
[Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes),
[Calendar reminders](https://developers.google.com/workspace/calendar/api/concepts/reminders),
[Jules sessions](https://jules.google/docs/api/reference/sessions/),
[Gemini API](https://ai.google.dev/api/generate-content),
[Railway volumes](https://docs.railway.com/volumes).
