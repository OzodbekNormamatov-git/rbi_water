// Operator uchun "Yangi buyurtma" sahifasi.
//
// Oqim:
//   1. Operator telefon raqamni kiritadi → tizim mijozni qidiradi
//   2. Topilsa — ism/balans avtomatik ko'rinadi (eski mijoz)
//      Topilmasa — operator ismni qo'shimcha kiritadi (yangi guest mijoz)
//   3. Mahsulot tanlash (cart-like UI: + / −)
//   4. Manzil tanlash (xaritadan yoki saqlangan — hozir faqat xarita)
//   5. Aloqa telefon va izoh
//   6. (Ixtiyoriy) keshbek va idishlar
//   7. "Buyurtmani yuborish" — kuryerlar guruhiga ketadi

import { api, ApiError } from "../api.js";
import { fmtMoney, fmtCount, escapeHtml } from "../format.js";
import { toast } from "../toast.js";

// Mini xarita modal — admin paneliga oddiy Leaflet bilan
const LEAFLET_CSS = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css";
const LEAFLET_JS  = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js";

let _leafletLoading = null;
function _loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  if (_leafletLoading) return _leafletLoading;
  _leafletLoading = new Promise((resolve, reject) => {
    if (!document.querySelector(`link[href="${LEAFLET_CSS}"]`)) {
      const link = document.createElement("link");
      link.rel = "stylesheet"; link.href = LEAFLET_CSS;
      document.head.appendChild(link);
    }
    const s = document.createElement("script");
    s.src = LEAFLET_JS;
    s.onload = () => resolve(window.L);
    s.onerror = () => reject(new Error("Xarita yuklanmadi."));
    document.head.appendChild(s);
  });
  return _leafletLoading;
}

// Nominatim — OpenStreetMap'ning bepul geocoding xizmati (kalit yo'q).
// Foydalanish siyosati: 1 so'rov/sekund (operator UI uchun yetarli).
const NOMINATIM = "https://nominatim.openstreetmap.org/search";

async function _geocode(query, { lat, lon } = {}) {
  const sp = new URLSearchParams({
    q: query, format: "json", limit: "6", "accept-language": "uz,ru,en",
  });
  // Toshkent atrofini ustun qilish — viewbox markazi xaritaning hozirgi joyi
  if (lat && lon) {
    const d = 0.5; // ~50 km radius
    sp.set("viewbox", `${lon - d},${lat + d},${lon + d},${lat - d}`);
  }
  const res = await fetch(`${NOMINATIM}?${sp}`, { headers: { "Accept": "application/json" } });
  if (!res.ok) throw new Error("Qidiruv xatosi");
  return await res.json();
}

function openMapPicker(initial) {
  return new Promise(async (resolve) => {
    const L = await _loadLeaflet().catch(() => null);
    if (!L) { resolve(null); return; }
    const start = initial || { latitude: 41.3111, longitude: 69.2797 }; // Tashkent
    const backdrop = document.createElement("div");
    backdrop.className = "map-picker-backdrop";
    backdrop.innerHTML = `
      <div class="map-picker-panel">
        <div class="map-picker-head">
          <b>Manzilni tanlang</b>
          <button class="map-picker-close" type="button" aria-label="Yopish">×</button>
        </div>
        <div class="map-picker-search">
          <input class="input" id="mp-search" type="text"
                 placeholder="Ko'cha yoki mahalla (masalan: Chilonzor, Amir Temur ko'chasi)" />
          <button class="btn btn--secondary" id="mp-search-btn" type="button">🔍</button>
        </div>
        <div class="map-picker-results" id="mp-results" hidden></div>
        <div class="map-picker-map" id="mp-map"></div>
        <div class="map-picker-pin">📍</div>
        <div class="map-picker-coord" id="mp-coord"></div>
        <div class="map-picker-foot">
          <button class="btn btn--secondary" id="mp-locate" type="button">📍 Mening joyim</button>
          <button class="btn" id="mp-ok" type="button">Tanlash</button>
        </div>
      </div>
    `;
    document.body.appendChild(backdrop);
    const map = L.map(backdrop.querySelector("#mp-map"), { attributionControl: false, zoomControl: true })
      .setView([start.latitude, start.longitude], 14);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
    const coordEl = backdrop.querySelector("#mp-coord");
    const refresh = () => {
      const c = map.getCenter();
      coordEl.textContent = `Lat: ${c.lat.toFixed(5)}, Lon: ${c.lng.toFixed(5)}`;
    };
    refresh(); map.on("move", refresh);
    const finish = (r) => { map.remove(); backdrop.remove(); resolve(r); };
    backdrop.querySelector(".map-picker-close").addEventListener("click", () => finish(null));
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop) finish(null); });
    backdrop.querySelector("#mp-ok").addEventListener("click", () => {
      const c = map.getCenter();
      finish({ latitude: c.lat, longitude: c.lng });
    });
    backdrop.querySelector("#mp-locate").addEventListener("click", () => {
      if (!navigator.geolocation) return;
      navigator.geolocation.getCurrentPosition(
        (pos) => map.setView([pos.coords.latitude, pos.coords.longitude], 16),
        () => {}, { enableHighAccuracy: true, timeout: 8000 },
      );
    });

    // ----- Qidiruv (Nominatim)
    const searchEl = backdrop.querySelector("#mp-search");
    const searchBtn = backdrop.querySelector("#mp-search-btn");
    const resultsEl = backdrop.querySelector("#mp-results");

    const doSearch = async () => {
      const q = searchEl.value.trim();
      if (q.length < 3) return;
      searchBtn.disabled = true;
      resultsEl.hidden = false;
      resultsEl.innerHTML = `<div class="map-picker-result-loading">Qidirilmoqda…</div>`;
      try {
        const center = map.getCenter();
        const results = await _geocode(q, { lat: center.lat, lon: center.lng });
        if (!results || !results.length) {
          resultsEl.innerHTML = `<div class="map-picker-result-empty">Topilmadi. Boshqa nom bilan urinib ko'ring.</div>`;
          return;
        }
        resultsEl.innerHTML = results.map((r, i) => {
          const parts = (r.display_name || "").split(",");
          const title = parts[0] || r.display_name;
          const sub = parts.slice(1).join(",").trim();
          return `
            <div class="map-picker-result" data-lat="${r.lat}" data-lon="${r.lon}">
              <div class="map-picker-result-title">${title}</div>
              <div class="map-picker-result-sub">${sub}</div>
            </div>
          `;
        }).join("");
        resultsEl.querySelectorAll(".map-picker-result").forEach((el) => {
          el.addEventListener("click", () => {
            const lat = Number(el.dataset.lat);
            const lon = Number(el.dataset.lon);
            map.setView([lat, lon], 17);
            resultsEl.hidden = true;
          });
        });
      } catch (e) {
        resultsEl.innerHTML = `<div class="map-picker-result-empty">Xatolik: ${e.message}</div>`;
      } finally {
        searchBtn.disabled = false;
      }
    };

    searchBtn.addEventListener("click", doSearch);
    searchEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); doSearch(); }
    });
    // Xaritani bossa — natijalar yashirinadi (yo'ldan olib tashlash)
    backdrop.querySelector(".map-picker-map").addEventListener("mousedown", () => {
      resultsEl.hidden = true;
    }, true);
  });
}

export async function renderOperatorOrder(root) {
  // State
  let products = [];
  const cart = new Map();        // food_id -> quantity
  let customer = null;            // { id, full_name, has_started_bot, cashback_balance, bottles_balance } | null
  let customerSearched = false;   // birinchi qidiruv qilinganmi
  let location = null;            // { latitude, longitude } | null
  let busy = false;

  root.innerHTML = `
    <div class="card" style="margin-bottom:14px">
      <h3 class="card__title">1. Mijoz ma'lumotlari</h3>
      <div class="form-row">
        <label class="label" for="op-phone">Telefon (mijozdan so'rang)</label>
        <div style="display:flex;gap:8px">
          <input class="input" id="op-phone" type="tel" placeholder="+998901234567" inputmode="tel" style="flex:1" />
          <button class="btn btn--secondary" id="op-lookup" type="button">🔍 Tekshirish</button>
        </div>
        <div class="muted" id="op-lookup-status" style="font-size:12px;margin-top:4px"></div>
      </div>
      <div class="form-row" id="op-name-row" hidden>
        <label class="label" for="op-name">Ism</label>
        <input class="input" id="op-name" type="text" placeholder="Mijozning ismi" />
      </div>
    </div>

    <div class="card" style="margin-bottom:14px">
      <h3 class="card__title">2. Mahsulotlar</h3>
      <div id="op-products" class="loading">Yuklanmoqda…</div>
      <div class="op-cart-summary" id="op-cart-summary" hidden>
        <span class="muted">Tanlangan:</span>
        <b id="op-cart-count">0 ta</b>
        <span style="margin-left:auto;font-weight:700;color:var(--brand-deep)" id="op-cart-total">0 so'm</span>
      </div>
    </div>

    <div class="card" style="margin-bottom:14px">
      <h3 class="card__title">3. Yetkazib berish manzili</h3>
      <button class="btn btn--secondary" id="op-pick-addr" type="button">🗺 Xaritadan tanlash</button>
      <div class="muted" id="op-addr-status" style="font-size:12px;margin-top:6px">Belgilanmagan</div>
      <label class="label" for="op-addr-details" style="margin-top:10px">Tafsilot (podyezd, kvartira...)</label>
      <input class="input" id="op-addr-details" type="text" maxlength="200" placeholder="3-podyezd, 17-kvartira" />
    </div>

    <div class="card" style="margin-bottom:14px">
      <h3 class="card__title">4. Aloqa va izoh</h3>
      <label class="label" for="op-contact">Aloqa telefoni (kuryer uchun)</label>
      <input class="input" id="op-contact" type="tel" inputmode="tel" placeholder="+998901234567" />
      <label class="label" for="op-note">Izoh (kuryer va oshpaz uchun)</label>
      <textarea class="textarea" id="op-note" maxlength="500" placeholder="Maxsus ko'rsatma, qo'ng'iroq qilmaslik va h.k."></textarea>
    </div>

    <div class="card" style="margin-bottom:14px" id="op-bonus-card" hidden>
      <h3 class="card__title">5. Keshbek bilan to'lash</h3>
      <div class="form-row">
        <label class="label" for="op-cashback">Keshbek ishlatish (so'm)</label>
        <input class="input" id="op-cashback" type="number" min="0" step="1000" placeholder="0" />
        <div class="muted" id="op-cashback-hint" style="font-size:12px;margin-top:4px"></div>
      </div>
      <div class="muted" style="font-size:12px;margin-top:8px">
        ℹ️ Bo'sh idishlar — yetkazganda kuryer kiritadi.
      </div>
    </div>

    <div style="display:flex;gap:10px;align-items:center;padding:12px 0">
      <span class="muted" id="op-summary"></span>
      <span style="flex:1"></span>
      <button class="btn" id="op-submit" type="button" style="min-width:180px">📦 Buyurtmani yuborish</button>
    </div>
  `;

  // Refs
  const phoneEl = root.querySelector("#op-phone");
  const lookupBtn = root.querySelector("#op-lookup");
  const lookupStatus = root.querySelector("#op-lookup-status");
  const nameRow = root.querySelector("#op-name-row");
  const nameEl = root.querySelector("#op-name");
  const productsEl = root.querySelector("#op-products");
  const cartSummary = root.querySelector("#op-cart-summary");
  const cartCountEl = root.querySelector("#op-cart-count");
  const cartTotalEl = root.querySelector("#op-cart-total");
  const pickAddrBtn = root.querySelector("#op-pick-addr");
  const addrStatus = root.querySelector("#op-addr-status");
  const addrDetailsEl = root.querySelector("#op-addr-details");
  const contactEl = root.querySelector("#op-contact");
  const noteEl = root.querySelector("#op-note");
  const bonusCard = root.querySelector("#op-bonus-card");
  const cashbackEl = root.querySelector("#op-cashback");
  const cashbackHint = root.querySelector("#op-cashback-hint");
  // Bo'sh idishlar (op-bottles) — UI'dan olib tashlangan: kuryer yetkazganda kiritadi.
  const submitBtn = root.querySelector("#op-submit");
  const summaryEl = root.querySelector("#op-summary");

  // ----- 1. Customer lookup
  async function doLookup() {
    const phone = phoneEl.value.trim();
    if (phone.length < 4) return toast("Telefon raqami juda qisqa", "error");
    lookupBtn.disabled = true;
    lookupStatus.textContent = "Qidirilmoqda…";
    try {
      const r = await api.operatorCustomerLookup(phone);
      customerSearched = true;
      if (r.found) {
        customer = r;
        lookupStatus.innerHTML = `✅ <b>${escapeHtml(r.full_name)}</b> — eski mijoz` +
          (r.has_started_bot ? " (botda)" : " (botsiz)");
        nameRow.hidden = true;
        nameEl.value = r.full_name;
        // Auto-fill contact phone
        if (!contactEl.value) contactEl.value = r.phone_number;
        updateBonusCard();
      } else {
        customer = null;
        lookupStatus.textContent = "❗️ Yangi mijoz — ismni kiriting";
        nameRow.hidden = false;
        nameEl.focus();
        if (!contactEl.value) contactEl.value = phone;
        updateBonusCard();
      }
    } catch (e) {
      lookupStatus.innerHTML = `<span style="color:var(--brand-danger)">${escapeHtml(e.message)}</span>`;
    } finally {
      lookupBtn.disabled = false;
    }
  }
  lookupBtn.addEventListener("click", doLookup);
  phoneEl.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doLookup(); } });

  // ----- 2. Products
  (async () => {
    try {
      // Operator buyurtma formasi uchun barcha aktiv mahsulotlar kerak (server
      // tomonida hozir li=200 max). Limit yetmasa, kelajakda bu yerni
      // ham `Yana yuklash` bilan paginatsiyalashtirish mumkin — hozircha
      // suv yetkazib berish shopida mahsulotlar soni ko'p emas.
      const res = await api.products({ limit: 200 });
      const items = Array.isArray(res) ? res : (res.items || []);
      products = items.filter((p) => p.is_available && !p.deleted_at);
      if (!products.length) {
        productsEl.innerHTML = `<div class="muted">Mahsulotlar yo'q.</div>`;
        return;
      }
      productsEl.classList.remove("loading");
      productsEl.innerHTML = `
        <div class="op-products-list">
          ${products.map((p) => `
            <div class="op-product-row" data-id="${p.id}">
              <div class="op-product-name">
                <div style="font-weight:600">${escapeHtml(p.name)}</div>
                <div class="muted" style="font-size:12px">${fmtMoney(p.price)}</div>
              </div>
              <div class="qty-stepper">
                <button type="button" data-act="dec">−</button>
                <div class="qty-stepper__value" data-qty>0</div>
                <button type="button" data-act="inc">+</button>
              </div>
            </div>
          `).join("")}
        </div>
      `;
      productsEl.querySelectorAll(".op-product-row").forEach((row) => {
        const id = Number(row.dataset.id);
        const qtyEl = row.querySelector("[data-qty]");
        row.querySelectorAll("button[data-act]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const act = btn.dataset.act;
            const cur = cart.get(id) || 0;
            const next = act === "inc" ? Math.min(cur + 1, 999) : Math.max(cur - 1, 0);
            if (next === 0) cart.delete(id); else cart.set(id, next);
            qtyEl.textContent = next;
            updateCartSummary();
          });
        });
      });
    } catch (e) {
      productsEl.innerHTML = `<div class="empty"><div class="empty__icon">⚠️</div><div class="empty__text">${escapeHtml(e.message)}</div></div>`;
    }
  })();

  function cartTotal() {
    let total = 0;
    for (const [id, qty] of cart) {
      const p = products.find((x) => x.id === id);
      if (p) total += Number(p.price) * qty;
    }
    return total;
  }
  function cartCount() {
    let c = 0;
    for (const v of cart.values()) c += v;
    return c;
  }
  function updateCartSummary() {
    const n = cartCount();
    if (n > 0) {
      cartSummary.hidden = false;
      cartCountEl.textContent = `${fmtCount(n)} ta`;
      cartTotalEl.textContent = fmtMoney(cartTotal());
    } else {
      cartSummary.hidden = true;
    }
    updateBonusCard();
    updateSummary();
  }

  // ----- 3. Address
  pickAddrBtn.addEventListener("click", async () => {
    const r = await openMapPicker(location || undefined);
    if (!r) return;
    location = r;
    addrStatus.innerHTML = `✅ <code>${r.latitude.toFixed(5)}, ${r.longitude.toFixed(5)}</code>`;
  });

  // ----- 4/5. Bonus card visibility — faqat mijozda keshbek bo'lsa ko'rinadi.
  // Bo'sh idishlar endi alohida widget emas — kuryer yetkazganda kiritadi.
  function updateBonusCard() {
    const cb = Number((customer && customer.cashback_balance) || 0);
    if (cb > 0) {
      bonusCard.hidden = false;
      const itemsTotal = cartTotal();
      cashbackHint.textContent = `Mavjud: ${fmtMoney(cb)} · 1000 so'm qadami bilan, items_total dan oshmasin (${fmtMoney(itemsTotal)})`;
      cashbackEl.max = String(Math.min(cb, itemsTotal));
    } else {
      bonusCard.hidden = true;
      cashbackEl.value = "0";
    }
  }

  function updateSummary() {
    const n = cartCount();
    if (!n) { summaryEl.textContent = ""; return; }
    const cb = Math.max(0, Math.floor((Number(cashbackEl.value) || 0) / 1000) * 1000);
    const cash = Math.max(0, cartTotal() - cb);
    summaryEl.innerHTML = `Jami: <b>${fmtMoney(cartTotal())}</b> · Naqd: <b style="color:var(--brand-deep)">${fmtMoney(cash)}</b>`;
  }
  cashbackEl.addEventListener("input", updateSummary);

  // ----- 6. Submit
  submitBtn.addEventListener("click", async () => {
    if (busy) return;
    const phone = phoneEl.value.trim();
    const name = (nameEl.value || (customer && customer.full_name) || "").trim();
    if (phone.length < 4) return toast("Telefon raqamini kiriting", "error");
    if (!customerSearched) return toast("Avval mijozni tekshiring (🔍 tugmasi)", "error");
    if (name.length < 2) return toast("Mijozning ismini kiriting", "error");
    if (cart.size === 0) return toast("Hech qaysi mahsulot tanlanmagan", "error");
    if (!location) return toast("Manzilni xaritadan tanlang", "error");
    const contact = contactEl.value.trim();
    if (contact.length < 4) return toast("Aloqa telefonini kiriting", "error");
    const note = noteEl.value.trim();
    if (note.length < 1) return toast("Izoh kiriting", "error");

    const items = Array.from(cart.entries()).map(([food_id, quantity]) => ({ food_id, quantity }));
    const cashbackToUse = Math.max(0, Math.floor((Number(cashbackEl.value) || 0) / 1000) * 1000);

    busy = true;
    submitBtn.disabled = true;
    submitBtn.textContent = "Yuborilmoqda…";
    try {
      const res = await api.operatorCreateOrder({
        customer_phone: phone,
        customer_full_name: name,
        items,
        latitude: location.latitude,
        longitude: location.longitude,
        address_label: "",
        address_details: addrDetailsEl.value.trim(),
        contact_phone: contact,
        note,
        cashback_to_use: cashbackToUse,
        // bottles_returned — yetkazganda kuryer kiritadi
      });
      toast(`✅ Buyurtma #${res.id} yaratildi — kuryerlarga yuborildi`, "success");
      // Formni tozalaymiz, lekin sahifani qaytadan render qilamiz
      setTimeout(() => renderOperatorOrder(root), 800);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Xatolik";
      toast(msg, "error");
    } finally {
      busy = false;
      submitBtn.disabled = false;
      submitBtn.textContent = "📦 Buyurtmani yuborish";
    }
  });
}
