import { api, ApiError } from "../api.js";
import { cart } from "../state.js";
import { fmtMoney, escapeHtml } from "../format.js";
import {
  hapticImpact,
  hideBackButton,
  hideMainButton,
  showConfirm,
} from "../telegram.js";
import { go } from "../router.js";
import { toast } from "../toast.js";
import { showCTA, hideCTA } from "../cta.js";

export function renderCart(root) {
  document.getElementById("screen-title").textContent = "Savatcha";
  hideBackButton();
  hideMainButton();

  // Skeleton
  root.innerHTML = `
    <div class="cart-list">
      ${Array.from({ length: 2 }).map(() => `
        <div class="cart-row">
          <div class="cart-row__thumb"><div class="skeleton" style="width:100%;height:100%"></div></div>
          <div class="cart-row__main">
            <div class="skeleton" style="height:14px;width:60%"></div>
            <div class="skeleton" style="height:12px;width:40%;margin-top:6px"></div>
          </div>
        </div>`).join("")}
    </div>
  `;

  let products = []; // cache
  let unsubscribe = null;

  const total = () =>
    products.reduce((sum, p) => sum + Number(p.price) * cart.qty(p.id), 0);

  const updateCTA = () => {
    if (cart.isEmpty()) {
      hideCTA();
      return;
    }
    showCTA(`Buyurtma berish · ${fmtMoney(total())}`, () => go("checkout"));
  };

  const renderRows = () => {
    if (cart.isEmpty()) {
      root.innerHTML = `
        <div class="empty">
          <div class="empty__icon">🛒</div>
          <div class="empty__text">Savatchangiz bo'sh.</div>
          <button class="btn btn--ghost" id="goShop" type="button">Mahsulotlarga o'tish</button>
        </div>`;
      root.querySelector("#goShop").addEventListener("click", () => go("products"));
      hideCTA();
      return;
    }

    const items = products.filter((p) => cart.qty(p.id) > 0);
    root.innerHTML = `
      <div class="cart-list">
        ${items.map((p) => {
          const q = cart.qty(p.id);
          const thumb = p.image_url
            ? `<img src="${escapeHtml(p.image_url)}" alt="" />`
            : `<span class="cart-row__thumb-fallback">💧</span>`;
          return `
            <div class="cart-row" data-id="${p.id}">
              <div class="cart-row__thumb">${thumb}</div>
              <div class="cart-row__main">
                <div class="cart-row__name">${escapeHtml(p.name)}</div>
                <div class="cart-row__sub">${fmtMoney(p.price)} × ${q}</div>
              </div>
              <div class="cart-row__qty">
                <button class="cart-row__btn" data-act="dec" type="button" aria-label="Kamaytirish">−</button>
                <div class="cart-row__count">${q}</div>
                <button class="cart-row__btn" data-act="inc" type="button" aria-label="Ko'paytirish">+</button>
              </div>
            </div>`;
        }).join("")}
      </div>

      <div class="summary">
        <div class="summary__label">Jami</div>
        <div class="summary__value">${fmtMoney(total())}</div>
      </div>

      <div class="spacer"></div>
      <button class="btn btn--danger" id="clearBtn" type="button">🗑 Savatchani tozalash</button>
    `;

    root.querySelectorAll(".cart-row").forEach((row) => {
      const id = Number(row.getAttribute("data-id"));
      row.querySelectorAll(".cart-row__btn").forEach((btn) => {
        btn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          hapticImpact("light");
          if (btn.dataset.act === "inc") cart.inc(id);
          else cart.dec(id);
        });
      });
    });

    root.querySelector("#clearBtn").addEventListener("click", async () => {
      const ok = await showConfirm("Savatcha tozalansinmi?");
      if (ok) {
        cart.clear();
        toast("Savatcha tozalandi");
      }
    });

    updateCTA();
  };

  (async () => {
    try {
      products = await api.products();
      renderRows();
      unsubscribe = cart.subscribe(renderRows);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Yuklab bo'lmadi";
      root.innerHTML = `<div class="empty"><div class="empty__icon">⚠️</div><div class="empty__text">${escapeHtml(msg)}</div></div>`;
      hideCTA();
    }
  })();

  return () => {
    if (unsubscribe) unsubscribe();
    hideBackButton();
    hideCTA();
  };
}
