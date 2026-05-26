// Moliyaviy hisobotlar — oylik (kunlar) + yillik (oylar).
//
// Rahbarning kunlik daromad oqimini, oyma-oy va yilma-yil ko'ra olishi uchun.

import { api, ApiError } from "../api.js";
import { fmtMoney, fmtCount, escapeHtml } from "../format.js";

let _charts = [];
function destroyCharts() {
  for (const c of _charts) { try { c.destroy(); } catch (_) {} }
  _charts = [];
}

const C = {
  blue:        "#0088CC",
  blueDark:    "#003F7F",
  green:       "#27AE60",
  yellow:      "#F39C12",
  blueAlpha:   "rgba(0, 136, 204, 0.15)",
};

const MONTH_NAMES = [
  "Yan", "Fev", "Mar", "Apr", "May", "Iyun",
  "Iyul", "Avg", "Sen", "Okt", "Noy", "Dek",
];
const MONTH_NAMES_FULL = [
  "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
  "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
];

function _years(centerYear, span = 4) {
  const arr = [];
  for (let y = centerYear - span; y <= centerYear; y++) arr.push(y);
  return arr;
}

export async function renderFinance(root, params) {
  destroyCharts();

  const now = new Date();
  // Lokal state — filter o'zgarganda o'zgartiriladi (let, not const).
  // Avval bu narsalar URL hash orqali boshqarilardi: handlerlar `location.hash`
  // qo'yib, router `hashchange` event bilan butun sahifani qayta render qilardi.
  // Lekin Telegram WebView (ayniqsa Desktop)'da hashchange ba'zan fire qilmaydi —
  // shu sababli `<select>` o'zgartirilsa ham UI yangilanmasdi. Endi:
  //   * Filter o'zgaradi → lokal state yangilanadi → reload() to'g'ridan-to'g'ri
  //   * URL faqat bookmark/share uchun replaceState bilan yangilanadi
  let year = Number(params.year) || now.getFullYear();
  let month = Number(params.month) || (now.getMonth() + 1);
  let view = params.view === "yearly" ? "yearly" : "monthly";

  root.innerHTML = `
    <div class="toolbar">
      <div class="filters">
        <div class="seg" id="seg">
          <button data-v="monthly" class="${view === "monthly" ? "active" : ""}" type="button">Oylik</button>
          <button data-v="yearly"  class="${view === "yearly"  ? "active" : ""}" type="button">Yillik</button>
        </div>
        <select class="select" id="yearSel">
          ${_years(now.getFullYear()).map((y) => `<option value="${y}" ${y === year ? "selected" : ""}>${y}</option>`).join("")}
        </select>
        <select class="select" id="monthSel" ${view === "yearly" ? "disabled" : ""}>
          ${MONTH_NAMES_FULL.map((nm, i) => `<option value="${i + 1}" ${i + 1 === month ? "selected" : ""}>${nm}</option>`).join("")}
        </select>
      </div>
    </div>

    <div class="kpi-grid" id="kpis"></div>

    <div class="charts-grid" style="grid-template-columns: 1fr">
      <div class="card chart-card">
        <h3 class="card__title" id="chartTitle">Yuklanmoqda…</h3>
        <canvas id="rev-chart"></canvas>
      </div>
    </div>
  `;

  const yearSel = root.querySelector("#yearSel");
  const monthSel = root.querySelector("#monthSel");
  const seg = root.querySelector("#seg");

  const reload = async () => {
    destroyCharts();
    const Chart = window.Chart;
    if (!Chart) {
      root.querySelector("#chartTitle").textContent = "Chart.js yuklanmadi";
      return;
    }

    let data;
    try {
      if (view === "yearly") {
        data = await api.financeYearly(year);
      } else {
        data = await api.financeMonthly(year, month);
      }
    } catch (e) {
      root.querySelector("#chartTitle").textContent = "Xatolik";
      root.querySelector("#kpis").innerHTML = `<div class="empty"><div class="empty__icon">⚠️</div><div class="empty__text">${escapeHtml(e.message)}</div></div>`;
      return;
    }

    const kpis = root.querySelector("#kpis");
    kpis.innerHTML = `
      <div class="kpi">
        <div class="kpi__icon">💵</div>
        <div class="kpi__label">Naqd daromad</div>
        <div class="kpi__value">${fmtMoney(data.cash_revenue)}</div>
        <div class="kpi__sub">Kuryerga yetib kelgan</div>
      </div>
      <div class="kpi">
        <div class="kpi__icon">💎</div>
        <div class="kpi__label">Keshbek aylanmasi</div>
        <div class="kpi__value">${fmtMoney(data.cashback_used)}</div>
        <div class="kpi__sub">+${fmtMoney(data.cashback_earned)} yangi liability</div>
      </div>
      <div class="kpi">
        <div class="kpi__icon">📊</div>
        <div class="kpi__label">Jami sotuv (gross)</div>
        <div class="kpi__value">${fmtMoney(data.gross_sale)}</div>
        <div class="kpi__sub">Naqd + keshbek</div>
      </div>
      <div class="kpi">
        <div class="kpi__icon">📦</div>
        <div class="kpi__label">Jami buyurtmalar</div>
        <div class="kpi__value">${fmtCount(data.total_orders)}</div>
        <div class="kpi__sub">${view === "yearly" ? "Yil davomida" : MONTH_NAMES_FULL[month - 1] + " " + year}</div>
      </div>
      <div class="kpi">
        <div class="kpi__icon">📈</div>
        <div class="kpi__label">O'rtacha buyurtma (naqd)</div>
        <div class="kpi__value">${fmtMoney(data.average_order)}</div>
        <div class="kpi__sub">Bitta buyurtmaga</div>
      </div>
    `;

    Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.color = "#64748B";

    if (view === "yearly") {
      root.querySelector("#chartTitle").textContent = `${year}-yil oylik moliyaviy hisobot`;
      const labels = data.months.map((m) => MONTH_NAMES[Number(m.month.split("-")[1]) - 1]);
      _charts.push(new Chart(root.querySelector("#rev-chart"), {
        type: "bar",
        data: {
          labels,
          datasets: [
            {
              label: "Naqd daromad",
              data: data.months.map((m) => m.cash_revenue),
              backgroundColor: C.blue,
              borderRadius: 6,
            },
            {
              label: "Keshbek aylanmasi",
              data: data.months.map((m) => m.cashback_used),
              backgroundColor: C.green,
              borderRadius: 6,
            },
          ],
        },
        options: stackedChartOptions(),
      }));
    } else {
      root.querySelector("#chartTitle").textContent = `${MONTH_NAMES_FULL[month - 1]} ${year} — kunlik moliyaviy hisobot`;
      const labels = data.days.map((d) => Number(d.date.split("-")[2]));
      _charts.push(new Chart(root.querySelector("#rev-chart"), {
        type: "line",
        data: {
          labels,
          datasets: [
            {
              label: "Naqd",
              data: data.days.map((d) => d.cash_revenue),
              borderColor: C.blue,
              backgroundColor: C.blueAlpha,
              fill: true,
              tension: 0.35,
              pointRadius: 0,
              pointHoverRadius: 4,
              borderWidth: 2.5,
            },
            {
              label: "Keshbek",
              data: data.days.map((d) => d.cashback_used),
              borderColor: C.green,
              backgroundColor: "rgba(39, 174, 96, 0.10)",
              fill: false,
              tension: 0.35,
              pointRadius: 0,
              pointHoverRadius: 4,
              borderWidth: 2,
              borderDash: [4, 3],
            },
          ],
        },
        options: chartOptions(),
      }));
    }
  };

  // URL'ni bookmark uchun yangilash + lokal reload. hashchange fire qilmaymiz —
  // shu sababli router butun sahifani qayta render qilmaydi (toza, tez).
  function updateUrlAndReload() {
    const sp = new URLSearchParams();
    sp.set("view", view);
    sp.set("year", String(year));
    sp.set("month", String(month));
    try {
      history.replaceState(
        null, "",
        `${location.pathname}${location.search}#/finance?${sp}`,
      );
    } catch (_) { /* iframe sandbox xato bersa — silent, reload baribir ishlaydi */ }
    reload();
  }

  seg.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-v]");
    if (!btn) return;
    const v = btn.dataset.v;
    if (v === view) return;
    view = v;
    // Segmented control visual state — aktiv tugmani ko'rsatish
    seg.querySelectorAll("button[data-v]").forEach((b) => {
      b.classList.toggle("active", b.dataset.v === view);
    });
    // Yillik rejimda month tanlash mantiqiy emas — disabled
    monthSel.disabled = (view === "yearly");
    updateUrlAndReload();
  });

  yearSel.addEventListener("change", () => {
    year = Number(yearSel.value) || year;
    updateUrlAndReload();
  });

  monthSel.addEventListener("change", () => {
    month = Number(monthSel.value) || month;
    updateUrlAndReload();
  });

  await reload();
  return () => destroyCharts();
}

function chartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: true, position: "bottom", labels: { boxWidth: 12, padding: 12 } },
      tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${formatMoneyAxis(ctx.parsed.y)}` } },
    },
    scales: {
      y: {
        beginAtZero: true,
        ticks: { callback: (v) => formatMoneyAxis(v) },
        grid: { color: "rgba(15,23,42,0.05)" },
      },
      x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true } },
    },
  };
}

function stackedChartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: true, position: "bottom", labels: { boxWidth: 12, padding: 12 } },
      tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${formatMoneyAxis(ctx.parsed.y)}` } },
    },
    scales: {
      y: {
        beginAtZero: true,
        stacked: true,
        ticks: { callback: (v) => formatMoneyAxis(v) },
        grid: { color: "rgba(15,23,42,0.05)" },
      },
      x: { stacked: true, grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true } },
    },
  };
}

function formatMoneyAxis(v) {
  v = Number(v) || 0;
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 1_000) return Math.round(v / 1_000) + "k";
  return String(v);
}
