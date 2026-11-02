// Cloudflare Pages Function: форма подписки → контакт в списке Brevo.
// Переменные проекта Pages (ставит workflow «сайт»): BREVO_API_KEY, BREVO_LIST_ID.
export async function onRequestPost({ request, env }) {
  const back = (q) => Response.redirect(new URL("/podpiska?" + q, request.url).toString(), 303);
  let email = "";
  try {
    const f = await request.formData();
    if (f.get("website")) return back("ok=1"); // ловушка для ботов
    email = String(f.get("email") || "").trim().toLowerCase();
  } catch (e) { return back("err=1"); }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 200) return back("err=1");
  const r = await fetch("https://api.brevo.com/v3/contacts", {
    method: "POST",
    headers: { "api-key": env.BREVO_API_KEY, "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({ email, listIds: [Number(env.BREVO_LIST_ID)], updateEnabled: true }),
  });
  return back(r.ok || r.status === 204 ? "ok=1" : "err=1");
}
