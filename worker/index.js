// Приёмник обратной связи BeamHub.
// Живёт на Cloudflare Workers, секреты держит у себя — в exe ничего не попадает.
//
// Каналы доставки (любой включённый работает, можно несколько сразу):
//   RESEND_KEY + FROM_EMAIL + TO_EMAIL — письмо через resend.com
//   BOT_TOKEN + CHAT_ID                — сообщение в Telegram-бота
//   GITHUB_TOKEN + GITHUB_REPO         — issue в репозитории (labels через GITHUB_LABELS)
//
// Проверка: curl -X POST https://<worker>/ -H 'content-type: application/json' \
//           -d '{"report":"тест","title":"проверка","version":"0.0"}'

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
    const version = String(body.version || "?");
    const ip = request.headers.get("cf-connecting-ip") || "?";
    const text = `${title}\n\n${report}\n\n---\nКонтакт: ${contact}\nВерсия: ${version}\nIP: ${ip}`;

    const sent = [];
    const failed = [];

    if (env.RESEND_KEY && env.FROM_EMAIL && env.TO_EMAIL) {
      const res = await fetch("https://api.resend.com/emails", {
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
      if (res.ok) sent.push("email");
      else failed.push(`email ${res.status}: ${(await res.text()).slice(0, 200)}`);
    }

    if (env.BOT_TOKEN && env.CHAT_ID) {
      const res = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          chat_id: env.CHAT_ID,
          text: text.slice(0, TG_LIMIT),
          disable_web_page_preview: true,
        }),
      });
      if (res.ok) sent.push("telegram");
      else failed.push(`telegram ${res.status}: ${(await res.text()).slice(0, 200)}`);
    }

    if (env.GITHUB_TOKEN && env.GITHUB_REPO) {
      const labels = (env.GITHUB_LABELS || "feedback").split(",").map((s) => s.trim());
      const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/issues`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${env.GITHUB_TOKEN}`,
          accept: "application/vnd.github+json",
          "content-type": "application/json",
          "user-agent": "BeamHub-feedback",
        },
        body: JSON.stringify({ title: `[feedback] ${title}`, body: report, labels }),
      });
      if (res.ok) sent.push("github");
      else failed.push(`github ${res.status}: ${(await res.text()).slice(0, 200)}`);
    }

    if (!sent.length) {
      const hint = failed.length
        ? failed.join("; ")
        : "не настроен ни один канал: добавь RESEND_KEY/TO_EMAIL, BOT_TOKEN/CHAT_ID или GITHUB_TOKEN";
      return json({ error: "delivery", detail: hint.slice(0, 400) }, 502);
    }
    return json({ ok: true, sent });
  },
};
