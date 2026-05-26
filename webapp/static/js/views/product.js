import { cart } from "../state.js";
import { fmtMoney, escapeHtml } from "../format.js";
import {
  hapticImpact,
  hideBackButton,
  hideMainButton,
  showBackButton,
} from "../telegram.js";
import { back, go } from "../router.js";
import { showCTA, hideCTA } from "../cta.js";

export function renderProduct(root, { product }) {
  if (!product) {
    back();
    return;
  }
  document.getElementById("screen-title").textContent = product.name;
  showBackButton(() => back());
  hideMainButton();

  const desc = (product.description || "").trim();
  const imgHtml = product.image_url
    ? `<img src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.name)}" />`
    : `<div class="product__hero-fallback">💧</div>`;

  root.innerHTML = `
    <div class="product">
      <div class="product__hero">${imgHtml}</div>
      <h2 class="product__name">${escapeHtml(product.name)}</h2>
      <div class="product__price">${fmtMoney(product.price)}</div>
      ${desc ? `<div class="product__desc">${escapeHtml(desc)}</div>` : ""}

      <div class="qty">
        <div class="qty__label">Miqdor</div>
        <div class="qty__controls">
          <button class="qty__btn" id="dec" type="button" aria-label="Kamaytirish">−</button>
          <div class="qty__value" id="val">${cart.qty(product.id) || 1}</div>
          <button class="qty__btn" id="inc" type="button" aria-label="Ko'paytirish">+</button>
        </div>
      </div>
    </div>
  `;

  const valEl = root.querySelector("#val");
  const decBtn = root.querySelector("#dec");
  const incBtn = root.querySelector("#inc");

  let qty = cart.qty(product.id) || 1;
  const refresh = () => {
    valEl.textContent = qty;
    decBtn.disabled = qty <= 1;
    incBtn.disabled = qty >= 999;
    const inCart = cart.qty(product.id) > 0;
    showCTA(
      `${inCart ? "Yangilash" : "Savatchaga qo'shish"} · ${fmtMoney(product.price * qty)}`,
      () => {
        hapticImpact("medium");
        cart.set(product.id, qty);
        go("cart");
      }
    );
  };

  decBtn.addEventListener("click", () => {
    if (qty > 1) { qty--; hapticImpact("light"); refresh(); }
  });
  incBtn.addEventListener("click", () => {
    if (qty < 999) { qty++; hapticImpact("light"); refresh(); }
  });

  refresh();

  return () => {
    hideBackButton();
    hideCTA();
  };
}
