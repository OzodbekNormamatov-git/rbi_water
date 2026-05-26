import { api } from "../api.js";
import { fmtMoney, fmtCount, fmtDate, escapeHtml } from "../format.js";
import { toast } from "../toast.js";
import { renderPagination, bindPagination } from "../pagination.js";

const PAGE_SIZE = 20;

export async function renderCustomers(root) {
  root.innerHTML = `
    <div class="filters" style="justify-content:space-between">
      <input class="input" id="search" placeholder="Ism yoki telefon bo'yicha izlash…" />
      <div class="muted" id="totalLabel" style="font-size:12px"></div>
    </div>
    <div class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th class="hide-narrow">#</th>
            <th>Ism</th>
            <th>Telefon</th>
            <th class="hide-narrow">Telegram ID</th>
            <th class="hide-narrow">Ro'yxatdan</th>
            <th style="text-align:right">Buyurtmalar</th>
            <th style="text-align:right">Jami</th>
            <th style="text-align:right">Keshbek</th>
            <th style="text-align:right">Idishlar</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="tbody"><tr><td colspan="10" class="loading">Yuklanmoqda…</td></tr></tbody>
      </table>
    </div>
    <div id="paginationWrap"></div>
  `;

  const tbody = document.getElementById("tbody");
  const search = document.getElementById("search");
  const totalLabel = document.getElementById("totalLabel");
  const paginationWrap = document.getElementById("paginationWrap");

  let timer = null;
  let cache = [];           // joriy sahifadagi mijozlar
  let total = 0;
  let currentQuery = "";
  let page = 1;             // 1-based
  let loading = false;

  function rowHtml(u) {
    return `
      <tr data-id="${u.id}">
        <td class="hide-narrow">${u.id}</td>
        <td><b>${escapeHtml(u.full_name)}</b></td>
        <td>${escapeHtml(u.phone_number)}</td>
        <td class="hide-narrow"><code>${u.telegram_id}</code></td>
        <td class="hide-narrow muted">${escapeHtml(fmtDate(u.created_at))}</td>
        <td style="text-align:right">${fmtCount(u.orders_count)}</td>
        <td style="text-align:right;font-weight:700">${fmtMoney(u.total_spent)}</td>
        <td style="text-align:right;color:var(--brand-primary);font-weight:600">${fmtMoney(u.cashback_balance)}</td>
        <td style="text-align:right;font-weight:600">${fmtCount(u.bottles_balance)}</td>
        <td style="text-align:right">
          <button class="btn btn--xs btn--secondary js-edit" type="button">⚙️</button>
        </td>
      </tr>
    `;
  }

  function bindRowHandlers() {
    tbody.querySelectorAll(".js-edit").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const tr = e.target.closest("tr");
        const id = Number(tr.getAttribute("data-id"));
        const u = cache.find((x) => x.id === id);
        if (u) openAdjust(u, () => loadPage());
      });
    });
  }

  function renderRows() {
    if (!cache.length) {
      tbody.innerHTML = `<tr><td colspan="10" class="empty"><div class="empty__icon">👤</div><div class="empty__text">Mijoz topilmadi.</div></td></tr>`;
      paginationWrap.innerHTML = "";
      totalLabel.textContent = "";
      return;
    }
    tbody.innerHTML = cache.map(rowHtml).join("");
    bindRowHandlers();

    totalLabel.textContent = `${fmtCount(total)} ta mijoz`;
    paginationWrap.innerHTML = renderPagination({ page, pageSize: PAGE_SIZE, total });
    bindPagination(paginationWrap, (newPage) => {
      page = newPage;
      loadPage();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  async function loadPage() {
    if (loading) return;
    loading = true;
    try {
      const offset = (page - 1) * PAGE_SIZE;
      const res = await api.customers(currentQuery, { limit: PAGE_SIZE, offset });
      const pageData = Array.isArray(res) ? { items: res, total: res.length } : res;
      cache = pageData.items || [];
      total = Number(pageData.total || 0);

      // Search filter o'zgartirilsa — joriy sahifa mavjud bo'lmasligi mumkin.
      // Oxirgi mavjud sahifaga qaytaramiz (UX'da "bo'sh sahifa" ko'rsatish o'rniga).
      if (cache.length === 0 && total > 0 && page > 1) {
        page = Math.max(1, Math.ceil(total / PAGE_SIZE));
        await loadPage();
        return;
      }
      renderRows();
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="10" class="empty"><div class="empty__icon">⚠️</div><div class="empty__text">${escapeHtml(e.message)}</div></td></tr>`;
      paginationWrap.innerHTML = "";
    } finally {
      loading = false;
    }
  }

  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      currentQuery = search.value.trim();
      page = 1;  // search o'zgartirilsa — 1-sahifaga qaytamiz
      loadPage();
    }, 280);
  });

  loadPage();
}


function openAdjust(u, onSaved) {
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML = `
    <div class="modal">
      <div class="modal__head">
        <h3 class="modal__title">${escapeHtml(u.full_name)}</h3>
        <button class="modal__close" type="button">×</button>
      </div>
      <div class="modal__body">
        <div class="balance-grid" style="margin-bottom:14px">
          <div class="balance-card">
            <div class="balance-card__label">Keshbek</div>
            <div class="balance-card__value" id="cbView">${fmtMoney(u.cashback_balance)}</div>
          </div>
          <div class="balance-card">
            <div class="balance-card__label">Bo'sh idishlar</div>
            <div class="balance-card__value" id="btView">${fmtCount(u.bottles_balance)}</div>
          </div>
        </div>

        <label class="label">Keshbekka qo'shish / ayirish (so'm)</label>
        <div style="display:flex;gap:8px">
          <input class="input" id="cbDelta" type="number" step="100" placeholder="Misol: 5000 yoki -1000" />
          <button class="btn btn--secondary" id="cbApply" type="button">Qo'llash</button>
        </div>
        <p class="muted" style="font-size:11px;margin-top:4px">Manfiy qiymat — ayirish. Yakuniy balans manfiy bo'lib qola olmaydi.</p>

        <label class="label" style="margin-top:14px">Idishlar (+/−)</label>
        <div style="display:flex;gap:8px">
          <input class="input" id="btDelta" type="number" step="1" placeholder="Misol: 3 yoki -2" />
          <button class="btn btn--secondary" id="btApply" type="button">Qo'llash</button>
        </div>
        <p class="muted" style="font-size:11px;margin-top:4px">Mijoz idish qaytarib oldi — manfiy; qo'shimcha berdi — musbat.</p>
      </div>
      <div class="modal__foot">
        <button class="btn" id="closeBtn" type="button">Yopish</button>
      </div>
    </div>
  `;
  document.body.appendChild(backdrop);

  const close = () => backdrop.remove();
  backdrop.querySelector(".modal__close").addEventListener("click", close);
  backdrop.querySelector("#closeBtn").addEventListener("click", close);
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });

  backdrop.querySelector("#cbApply").addEventListener("click", async () => {
    const delta = Number(backdrop.querySelector("#cbDelta").value);
    if (!Number.isFinite(delta) || delta === 0) {
      return toast("Qiymat kiriting", { error: true });
    }
    try {
      const r = await api.adjustCashback(u.id, { delta, reason: "admin manual" });
      backdrop.querySelector("#cbView").textContent = fmtMoney(r.cashback_balance);
      backdrop.querySelector("#cbDelta").value = "";
      toast("Keshbek yangilandi");
      onSaved && onSaved();
    } catch (e) {
      toast(e.message || "Xatolik", { error: true });
    }
  });

  backdrop.querySelector("#btApply").addEventListener("click", async () => {
    const delta = Number(backdrop.querySelector("#btDelta").value);
    if (!Number.isFinite(delta) || delta === 0) {
      return toast("Qiymat kiriting", { error: true });
    }
    try {
      const r = await api.adjustBottles(u.id, { delta, reason: "admin manual" });
      backdrop.querySelector("#btView").textContent = fmtCount(r.bottles_balance);
      backdrop.querySelector("#btDelta").value = "";
      toast("Idishlar balansi yangilandi");
      onSaved && onSaved();
    } catch (e) {
      toast(e.message || "Xatolik", { error: true });
    }
  });
}
