// Будильник Cui Bono? на Cloudflare: точно по времени запускает workflow бота в GitHub.
// Расписание GitHub Actions теряет запуски (2 ноября 2026 из 10 сработал один), поэтому основной запуск — отсюда.
// Слот = cron из расписания GitHub; бот публикует слот один раз, запасной запуск GitHub его пропускает.
// Время в UTC (МСК = UTC+3).
const SLOTS = [
  // заметки: 08:37, 10:37, 12:37, 17:07, 19:37, 22:07 МСК
  { cron: "37 5 * * *", wf: "post.yml" },
  { cron: "37 7 * * *", wf: "post.yml" },
  { cron: "37 9 * * *", wf: "post.yml" },
  { cron: "7 14 * * *", wf: "post.yml" },
  { cron: "37 16 * * *", wf: "post.yml" },
  { cron: "7 19 * * *", wf: "post.yml" },
  // рубрики
  { cron: "52 7 * * 1", wf: "formats.yml", format: "indicators" }, // пн 10:52 Показатели
  { cron: "22 8 * * *", wf: "formats.yml", format: "analysis" },   // 11:22 Разбор
  { cron: "52 9 * * 6", wf: "formats.yml", format: "week" },       // сб 12:52 Неделя
  { cron: "52 10 * * *", wf: "formats.yml", format: "position" },  // 13:52 Позиция
  { cron: "22 12 * * *", wf: "formats.yml", format: "analysis" },  // 15:22 запасной Разбор
  { cron: "52 17 * * *", wf: "formats.yml", format: "position" },  // 20:52 Позиция
];
const REPO = "Unzeabk/cui-bot";

function due(cron, d) {
  const [m, h, , , dow] = cron.split(" ");
  return +m === d.getUTCMinutes() && +h === d.getUTCHours() && (dow === "*" || +dow === d.getUTCDay());
}

async function dispatch(env, s) {
  const inputs = { slot: s.cron, dry_run: "false" };
  if (s.format) inputs.format = s.format;
  for (let i = 0; i < 3; i++) {
    const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${s.wf}/dispatches`, {
      method: "POST",
      headers: { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json", "User-Agent": "cui-alarm" },
      body: JSON.stringify({ ref: "main", inputs }),
    });
    if (r.status === 204) { console.log("запущен", s.wf, s.cron); return; }
    console.log("ошибка", s.wf, s.cron, r.status, await r.text());
    await new Promise((ok) => setTimeout(ok, 5000));
  }
}

export default {
  async scheduled(event, env, ctx) {
    const d = new Date(event.scheduledTime);
    for (const s of SLOTS) if (due(s.cron, d)) ctx.waitUntil(dispatch(env, s));
  },
  async fetch() {
    return new Response("cui-alarm: ok\n" + SLOTS.map((s) => `${s.cron}  ${s.wf} ${s.format || ""}`).join("\n"));
  },
};
