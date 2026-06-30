// Kuryer Mini App — mavjud buyurtmalar (polling), claim (race-safe), transitsiyalar, statistika.

const tg = window.Telegram && window.Telegram.WebApp;
if (tg) { try { tg.ready(); tg.expand(); } catch (_) {} }
const initData = tg ? tg.initData : "";

const screen = document.getElementById("screen");
const tabsEl = document.getElementById("tabs");
const toastEl = document.getElementById("toast");
const availBadge = document.getElementById("availBadge");

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const money = (n) => (Math.round(Number(n) || 0)).toLocaleString("ru-RU").replace(/,/g, " ") + " so'm";

let _toastT = null;
function toast(msg, isErr) {
  toastEl.textContent = msg;
  toastEl.className = isErr ? "show err" : "show";
  clearTimeout(_toastT);
  _toastT = setTimeout(() => { toastEl.className = ""; }, 2600);
  if (tg && tg.HapticFeedback) { try { tg.HapticFeedback.notificationOccurred(isErr ? "error" : "success"); } catch (_) {} }
}

async function api(path, { method = "GET", body } = {}) {
  const headers = { "Authorization": `tma ${initData}`, "Accept": "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch (_) {
    throw new Error("Tarmoq xatosi");
  }
  let data = null;
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error((data && (data.message || data.detail)) || `Xatolik (${res.status})`);
  return data;
}

// ---------------------- State + tabs ----------------------
let tab = "available";
let pollTimer = null;

tabsEl.querySelectorAll("button[data-tab]").forEach((b) => {
  b.addEventListener("click", () => switchTab(b.dataset.tab));
});

function switchTab(next) {
  tab = next;
  tabsEl.querySelectorAll("button[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (tab === "available") { renderAvailable(); pollTimer = setInterval(renderAvailable, 5000); }
  else if (tab === "active") { renderActive(); pollTimer = setInterval(renderActive, 5000); }
  else { renderStats(); }
}

function loading() { screen.innerHTML = `<div class="empty"><div class="empty__icon">⏳</div>Yuklanmoqda…</div>`; }
function errorBox(msg) {
  screen.innerHTML = `<div class="empty"><div class="empty__icon">⚠️</div>${esc(msg)}</div>`;
}

// ---------------------- Available (NEW orders) ----------------------
let _availFirst = true;
async function renderAvailable() {
  if (_availFirst) loading();
  let list;
  try { list = await api("/api/courier/available"); }
  catch (e) { if (_availFirst) errorBox(e.message); return; }
  _availFirst = false;
  availBadge.hidden = !(list && list.length);
  if (list && list.length) availBadge.textContent = String(list.length);
  if (!list || !list.length) {
    screen.innerHTML = `<div class="empty"><div class="empty__icon">📭</div>Hozircha yangi buyurtma yo'q.<div class="muted" style="margin-top:6px">Yangi buyurtma kelsa shu yerda chiqadi.</div></div>`;
    return;
  }
  screen.innerHTML = list.map((o) => `
    <div class="card">
      <div class="row"><span class="pill pill--new">🆕 ${esc(o.display_number)}</span><span style="flex:1"></span><span class="total">${money(o.total_amount)}</span></div>
      <div class="items">${o.items.map((it) => `${esc(it.food_name)} × ${it.quantity}`).join(", ")}</div>
      ${o.address_details ? `<div class="addr">📍 ${esc(o.address_details)}</div>` : ""}
      <div class="muted">🗺 <a class="tel" href="${esc(o.map_url)}" target="_blank">Xaritada ko'rish</a></div>
      ${o.note ? `<div class="muted">📝 ${esc(o.note)}</div>` : ""}
      <button class="btn btn--claim" data-claim="${o.id}">✅ Men olaman</button>
    </div>`).join("");
  screen.querySelectorAll("[data-claim]").forEach((b) => b.addEventListener("click", () => claim(Number(b.dataset.claim), b)));
}

async function claim(orderId, btn) {
  btn.disabled = true; btn.textContent = "Olinmoqda…";
  try {
    await api(`/api/courier/orders/${orderId}/claim`, { method: "POST" });
    toast("✅ Buyurtma sizniki!");
    switchTab("active");
  } catch (e) {
    // 409 — boshqa kuryer ulgurdi yoki sizda tugallanmagan buyurtma bor.
    toast(e.message, true);
    renderAvailable();  // ro'yxatni yangilaymiz (olingan buyurtma yo'qoladi)
  }
}

// ---------------------- Active (my order) ----------------------
let _bottles = 0;
async function renderActive() {
  let list;
  try { list = await api("/api/courier/active"); }
  catch (e) { errorBox(e.message); return; }
  if (!list || !list.length) {
    screen.innerHTML = `<div class="empty"><div class="empty__icon">🚚</div>Sizda hozir faol buyurtma yo'q.<div class="muted" style="margin-top:6px">"Mavjud" bo'limidan buyurtma oling.</div></div>`;
    return;
  }
  const o = list[0];
  const st = o.status;
  let actions = "";
  if (st === "ACCEPTED") {
    actions = `<button class="btn btn--go" data-act="delivering" data-id="${o.id}">🚗 Yo'lga chiqdim</button>`;
  } else if (st === "DELIVERING") {
    actions = `<button class="btn btn--go" data-act="arrived" data-id="${o.id}">📍 Yetib keldim</button>`;
  } else if (st === "ARRIVED") {
    _bottles = Number(o.bottles_returned || 0);
    actions = `
      <div class="muted" style="margin-top:10px">♻️ Mijozdan olingan bo'sh idishlar:</div>
      <div class="stepper">
        <button data-bottle="dec">−</button>
        <div class="val" id="btlVal">${_bottles}</div>
        <button data-bottle="inc">+</button>
      </div>
      <button class="btn btn--ok" data-act="delivered" data-id="${o.id}">✅ Yetkazib berildi — yopish</button>`;
  }
  screen.innerHTML = `
    <div class="card">
      <div class="row"><span class="pill pill--act">${esc(o.status_label)}</span><span style="flex:1"></span><span class="total">${money(o.total_amount)}</span></div>
      <div class="muted" style="margin-top:2px">${esc(o.display_number)}</div>
      <div class="items">${o.items.map((it) => `${esc(it.food_name)} × ${it.quantity}`).join(", ")}</div>
      ${o.address_details ? `<div class="addr">📍 ${esc(o.address_details)}</div>` : ""}
      <div class="row" style="gap:14px;margin-top:4px">
        ${o.contact_phone ? `<a class="tel" href="tel:${esc(o.contact_phone)}">📞 ${esc(o.contact_phone)}</a>` : ""}
        <a class="tel" href="${esc(o.map_url)}" target="_blank">🗺 Xarita</a>
      </div>
      ${o.note ? `<div class="muted" style="margin-top:4px">📝 ${esc(o.note)}</div>` : ""}
      ${actions}
    </div>`;
  // Stepper
  const valEl = document.getElementById("btlVal");
  screen.querySelectorAll("[data-bottle]").forEach((b) => b.addEventListener("click", () => {
    _bottles = b.dataset.bottle === "inc" ? _bottles + 1 : Math.max(0, _bottles - 1);
    if (valEl) valEl.textContent = String(_bottles);
  }));
  // Actions
  screen.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => doAction(b.dataset.act, Number(b.dataset.id), b)));
}

async function doAction(act, orderId, btn) {
  btn.disabled = true;
  try {
    if (act === "delivered") {
      // Avval bo'sh idish sonini saqlaymiz, keyin yopamiz.
      await api(`/api/courier/orders/${orderId}/bottles`, { method: "POST", body: { value: _bottles } });
      await api(`/api/courier/orders/${orderId}/delivered`, { method: "POST" });
      toast("📦 Yetkazildi. Rahmat!");
      switchTab("available");
      return;
    }
    await api(`/api/courier/orders/${orderId}/${act}`, { method: "POST" });
    if (act === "arrived") toast("📍 Mijozga xabar yuborildi");
    else toast("🚗 Yo'lga chiqdingiz");
    renderActive();
  } catch (e) {
    toast(e.message, true);
    renderActive();
  }
}

// ---------------------- Stats ----------------------
async function renderStats() {
  loading();
  let s;
  try { s = await api("/api/courier/stats"); }
  catch (e) { errorBox(e.message); return; }
  const cash = Number(s.cash_balance || 0);
  screen.innerHTML = `
    <div class="card">
      <div style="font-weight:700;margin-bottom:8px">📊 Yetkazib berilgan buyurtmalar</div>
      <div class="row"><span class="muted" style="flex:1">Bugun</span><span class="num">${s.today}</span></div>
      <div class="row"><span class="muted" style="flex:1">Shu oyda</span><span class="num">${s.month}</span></div>
      <div class="row"><span class="muted" style="flex:1">Shu yilda</span><span class="num">${s.year}</span></div>
      <div class="row"><span class="muted" style="flex:1">Hammasi</span><span class="num">${s.total}</span></div>
    </div>
    ${cash > 0 ? `
    <div class="card" style="border-color:#fcd34d;background:#fffbeb">
      <div style="font-weight:700">💵 Qo'lingizdagi naqd</div>
      <div class="total" style="margin-top:4px">${money(cash)}</div>
      <div class="muted" style="margin-top:4px">Bu summani kompaniyaga topshirishingiz kerak.</div>
    </div>` : ""}`;
}

// ---------------------- Boot ----------------------
(async () => {
  let me;
  try { me = await api("/api/courier/me"); }
  catch (e) {
    errorBox(e.message + "\nKuryer botiga /start yuborganingizni va admin sizni aktiv qilganini tekshiring.");
    return;
  }
  // Faol buyurtmasi bo'lsa, darhol "Menikim" tabini ochamiz.
  switchTab(me.active_order_id ? "active" : "available");
})();
