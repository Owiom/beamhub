# Приёмник обратной связи BeamHub

Маленький воркер для Cloudflare Workers: принимает отчёт из приложения и доставляет
его тебе — письмом, в Telegram или issue в GitHub. Токены живут в секретах воркера,
в exe уходит только его адрес.

## Развернуть в два клика

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https://github.com/Owiom/beamhub/tree/main/worker)

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
