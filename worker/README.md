# Приёмник обратной связи BeamHub

Маленький воркер для Cloudflare Workers: принимает отчёт из приложения и доставляет
его тебе — письмом, в Telegram или issue в GitHub. Токены живут в секретах воркера,
в exe уходит только его адрес.

## Развернуть в два клика

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https://github.com/Owiom/beamhub/tree/main/worker)

Кнопка открывает мастер Cloudflare: он попросит войти в аккаунт, дать доступ к GitHub
и создаст воркер `beamhub-feedback`. Если после нажатия «что-то прошло», но адреса нет —
проверь [dash.cloudflare.com](https://dash.cloudflare.com) → **Workers & Pages**:
воркер либо уже в списке (открой его и скопируй URL), либо мастер не дошёл до конца.

### Если кнопка не сработала — скрипт

```powershell
powershell -ExecutionPolicy Bypass -File worker\deploy.ps1
```

Скрипт спрашивает API-токен Cloudflare (ввод скрыт, никуда не сохраняется), сам
загружает код, ставит секреты и печатает адрес воркера. Нужен токен с правами
**Account → Workers Scripts: Edit** и **Account → Account Settings: Read**
([создать](https://dash.cloudflare.com/profile/api-tokens)).

### Или руками, без кнопки

1. dash.cloudflare.com → **Workers & Pages** → **Create** → **Worker** → имя `beamhub-feedback` → **Deploy**.
2. **Edit code** → удали шаблон → вставь весь [`index.js`](index.js) → **Deploy**.
3. **Settings → Variables and Secrets** → добавь секреты канала (таблица ниже).
4. Вверху страницы воркера будет URL `https://beamhub-feedback.<аккаунт>.workers.dev`.

Дальше — **Settings → Variables and Secrets**, добавь секреты для одного или нескольких каналов.

### Куда доставлять

| Канал | Что нужно | Плюсы |
|---|---|---|
| **Почта** (Resend) | `RESEND_KEY`, `FROM_EMAIL`, `TO_EMAIL` | работает везде, письмо можно фильтровать; бесплатно 100 писем/сутки на [resend.com](https://resend.com) |
| **GitHub issue** | `GITHUB_TOKEN`, `GITHUB_REPO` (`Owiom/beamhub`), по желанию `GITHUB_LABELS` | отчёты становятся задачами, видна история |
| **Telegram** | `BOT_TOKEN`, `CHAT_ID` | мгновенно; но учти: `api.telegram.org` в РФ часто заблокирован, проверять с VPN |

Почта — самый предсказуемый вариант для русскоязычной аудитории.
GitHub — удобно, если любишь вести всё в issues.

### Почта через Resend

1. Зарегистрируйся на [resend.com](https://resend.com) (бесплатно).
2. **API Keys → Create** → скопируй ключ (`re_...`).
3. Свой домен подтверждать не обязательно для теста: `FROM_EMAIL=onboarding@resend.dev`,
   `TO_EMAIL=sparco56@gmail.com` — Resend разрешает слать на свой адрес.
4. Для красоты можно добавить домен и слать с `beamhub@твой-домен`.

### GitHub issue

1. Создай fine-grained токен: **Settings → Developer settings → Personal access tokens
   → Fine-grained** → доступ только к `Owiom/beamhub` → `Issues: Read and write`.
2. В воркер: `GITHUB_TOKEN=<токен>`, `GITHUB_REPO=Owiom/beamhub`, `GITHUB_LABELS=feedback`.

Токен в exe не попадает, поэтому спам ограничен только воркером (honeypot + лимиты).

## Подключить к приложению

1. Скопируй адрес воркера: `https://beamhub-feedback.<аккаунт>.workers.dev`.
2. BeamHub → **Настройки → «Приёмник обратной связи»** → вставь адрес → Сохранить.
3. **Обратная связь → Отправить** — в окне появится «Отчёт отправлен», а у тебя письмо/issue.

## Проверка без приложения

```bash
curl -X POST https://beamhub-feedback.<аккаунт>.workers.dev \
  -H 'content-type: application/json' \
  -d '{"report":"тест","title":"проверка","version":"0.0"}'
```

Ответ `{"ok":true,"sent":["email"]}` — канал сработал. Если `502` — в `detail`
будет причина (например, `telegram 401` или `email 403`).

## Ограничения

- отчёт — до 12 000 символов, тело запроса — до 14 КБ;
- принимается только `POST` с JSON, honeypot-поле отсекает простых ботов;
- если нужен лимит по IP, добавь KV-namespace и счётчик (пример в
  [docs/FEEDBACK.md](../docs/FEEDBACK.md#защита-от-мусора)).
