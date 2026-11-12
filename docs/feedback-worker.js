// Приёмник обратной связи BeamHub для Cloudflare Workers.
// Кладёт отчёт в Telegram автора. Секреты — только в настройках воркера,
// в exe ничего не попадает.
//
// Переменные окружения:
//   BOT_TOKEN  — токен бота от @BotFather
//   CHAT_ID    — куда присылать (свой id или id канала)
//   RESEND_KEY — необязательно: ключ resend.com, тогда отчёт уходит ещё и письмом
//   FROM_EMAIL — адрес отправителя для Resend

const LIMIT = 12000;
const TG_LIMIT = 3800;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "access-control-allow-headers": "content-type",
      "access-control-allow-methods": "POST, OPTIONS",
    },
  });
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return json({ ok: true });
    if (request.method !== "POST") return json({ error: "method" }, 405);

    const size = Number(request.headers.get("content-length") || 0);
    if (size > LIMIT + 2000) return json({ error: "too big" }, 413);

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "json" }, 400);
    }

    // ловушка для ботов: человек это поле не заполняет
    if (body.honeypot) return json({ ok: true });

    const report = String(body.report || "").slice(0, LIMIT);
    if (!report.trim()) return json({ error: "empty" }, 400);

    const title = String(body.title || "Без заголовка").slice(0, 120);
    const contact = String(body.contact || "").slice(0, 120) || "не указан";
    const text =
      `${title}\n\n${report}\n\n` +
      `---\nКонтакт: ${contact}\n` +
      `Версия: ${body.version || "?"}\nIP: ${request.headers.get("cf-connecting-ip") || "?"}`;

    if (env.BOT_TOKEN && env.CHAT_ID) {
      const tg = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          chat_id: env.CHAT_ID,
          text: text.slice(0, TG_LIMIT),
          disable_web_page_preview: true,
        }),
      });
      if (!tg.ok) {
        const detail = await tg.text();
        return json({ error: "telegram", detail: detail.slice(0, 300) }, 502);
      }
    }

    if (env.RESEND_KEY && env.FROM_EMAIL && env.TO_EMAIL) {
      await fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: {
          authorization: `Bearer ${env.RESEND_KEY}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          from: env.FROM_EMAIL,
          to: env.TO_EMAIL,
          subject: `BeamHub: ${title}`,
          text,
        }),
      });
    }

    return json({ ok: true });
  },
};
