"use strict";
/* Экран программы. Вся разметка строится через h() с textContent: данные из документов не выполняются как код. */

// ------------------------------------------------------------------ помощники
const $ = (s, r = document) => r.querySelector(s);

function add(e, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    e.append(k.nodeType ? k : document.createTextNode(String(k)));
  }
}
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (k === "value") e.value = v;
      else if (k === "checked" || k === "disabled" || k === "hidden") e[k] = !!v;
      else e.setAttribute(k, v === true ? "" : v);
    }
  }
  add(e, kids);
  return e;
}
const chip = (text, cls) => h("span", { class: "chip " + (cls || "gray") }, text);
const fd = (iso) => (iso && /^\d{4}-\d{2}-\d{2}/.test(iso) ? iso.slice(8, 10) + "." + iso.slice(5, 7) + "." + iso.slice(0, 4) : "—");
const num = (n) => (n == null ? "—" : Number(n).toLocaleString("ru-RU"));
const money = (v) => (v == null || v === "" ? "—" : Number(v).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ₽");
function plural(n, a, b, c) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return a;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return b;
  return c;
}
function dlChip(days, date) {
  if (days == null) return h("span", { class: "muted" }, "—");
  if (days < 0) return chip("просрочен на " + -days + " " + plural(-days, "день", "дня", "дней"), "red");
  if (days === 0) return chip("сегодня", "red");
  if (days <= 30) return chip("через " + days + " " + plural(days, "день", "дня", "дней"), "amb");
  return chip(fd(date), "gray");
}
function freshChip(f) {
  if (!f) return null;
  if (f.level === "none") return chip("Не разбирался", "gray");
  return chip(f.label + " · " + f.ago, f.cls);
}
async function api(path, body, opt = {}) {
  const init = { method: body !== undefined || opt.post ? "POST" : "GET" };
  if (body instanceof Blob) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  let r;
  try { r = await fetch("/api/" + path, init); } catch (e) { throw new Error("Нет связи с программой. Возможно, она закрыта."); }
  let j = null;
  try { j = await r.json(); } catch (e) { /* не JSON */ }
  if (!r.ok) throw new Error((j && j.error) || "Ошибка " + r.status);
  return j;
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

function openModal(content) {
  const m = $("#modal");
  m.replaceChildren(h("div", { class: "box", role: "dialog", "aria-modal": "true" }, content));
  m.hidden = false;
  m.onclick = (e) => { if (e.target === m) closeModal(); };
}
function closeModal() { $("#modal").hidden = true; $("#modal").replaceChildren(); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

function errBox(msg) { return h("div", { class: "banner red" }, h("div", null, h("b", null, "Что-то пошло не так"), msg)); }

// ------------------------------------------------------------------ задачи
const tracked = new Map(); // id -> {resolve, last}
async function pollJobs() {
  try {
    const running = await api("jobs");
    for (const j of running) if (!tracked.has(j.id)) tracked.set(j.id, { cbs: [], last: j });
    const box = $("#jobs");
    for (const [id, t] of [...tracked]) {
      const j = await api("job/" + id);
      t.last = j;
      t.cbs.forEach((cb) => cb(j));
      if (j.state !== "running") tracked.delete(id);
    }
    const shown = [...tracked.values()].map((t) => jobView(t.last));
    box.replaceChildren(...shown);
  } catch (e) { /* связь пропала — покажет ping */ }
}
function jobView(j) {
  const pct = j.total ? Math.min(100, Math.round((j.done / j.total) * 100)) : null;
  return h("div", { class: "job" },
    h("div", { class: "t" },
      h("b", null, j.title),
      h("button", { class: "btn btn-s", onclick: () => api("job/" + j.id + "/cancel", {}) }, "Остановить")),
    pct != null ? h("div", { class: "bar" }, h("i", { style: "width:" + pct + "%" })) : h("div", { class: "bar" }, h("i", { style: "width:35%" })),
    h("div", { class: "small muted mt" },
      pct != null ? j.done + " из " + j.total : num(j.done) + " строк", j.msg ? " · " + j.msg : "", " · " + j.seconds + " с"));
}
function watchJob(id) {
  return new Promise((resolve) => {
    const t = tracked.get(id) || { cbs: [], last: null };
    tracked.set(id, t);
    t.cbs.push((j) => { if (j.state !== "running") resolve(j); });
    pollJobs();
  });
}
setInterval(pollJobs, 1000);
setInterval(() => api("ping").catch(() => {
  $("#view").prepend(errBox("Программа закрыта или остановлена. Запустите её снова с ярлыка."));
}), 5000);
api("ping").catch(() => {});

// ------------------------------------------------------------------ навигация
const NAV = [
  ["#/", "Сводка", "M3 12l9-8 9 8M5 10v10h14V10"],
  ["#/portfolio", "Портфель", "M4 6h16M4 12h16M4 18h16"],
  ["#/batch", "Загрузка документов", "M12 16V4m0 0l-4 4m4-4l4 4M4 20h16"],
  ["#/folders", "Подключённые папки", "M3 7h6l2 2h10v10H3z"],
  ["#/deadlines", "Сроки", "M12 7v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "urgent"],
  ["#/ip", "Исполнительные производства", "M5 4h10l4 4v12H5zM14 4v5h5"],
  ["#/templates", "Шаблоны документов", "M6 3h9l4 4v14H6zM9 12h7M9 16h7"],
  ["#/dossiers", "Судебные досье", "M4 7h6l2 2h8v11H4zM4 4h12"],
  ["#/interest", "Калькулятор процентов", "M5 3h14v18H5zM8 7h8M8 11h2m4 0h2M8 15h2m4 0h2"],
  ["#/practice", "Судебная практика", "M4 3h16v18H4zM8 7h8M8 11h8M8 15h5"],
  ["#/tools", "Справочники и расчёты", "M3 5h18M3 12h18M3 19h18"],
  ["#/settings", "Настройки", "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM4 12h2m12 0h2M12 4v2m0 12v2"],
];
function renderNav(active, counts) {
  const nav = $("#nav");
  nav.replaceChildren(...NAV.map(([href, label, path, badge]) => {
    const on = active === href || (href !== "#/" && active.startsWith(href));
    const c = badge && counts ? counts[badge] : 0;
    return h("a", { class: "nav-i" + (on ? " on" : ""), href },
      h("svg", { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round" }),
      label, c ? h("span", { class: "cnt" }, num(c)) : null);
  }));
  // svg-иконки: path нельзя создать через createElement без namespace, поэтому собираем отдельно
  nav.querySelectorAll("a").forEach((a, i) => {
    const old = a.querySelector("svg");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [k, v] of [["width", 18], ["height", 18], ["viewBox", "0 0 24 24"], ["fill", "none"], ["stroke", "currentColor"], ["stroke-width", 1.8], ["stroke-linecap", "round"], ["stroke-linejoin", "round"]]) svg.setAttribute(k, v);
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", NAV[i][2]);
    svg.append(p);
    old.replaceWith(svg);
  });
}

const ui = { pf: { q: "", sit: "", fresh: "", page: 1 }, dl: { kind: "", period: "90", page: 1 }, ip: { filter: "stalled", page: 1 } };
let navToken = 0;

async function route() {
  const hash = location.hash || "#/";
  const tok = ++navToken;
  const [path, qs] = hash.split("?");
  const parts = path.replace(/^#\/?/, "").split("/").filter(Boolean);
  const params = new URLSearchParams(qs || "");
  const view = $("#view");
  closeModal();
  view.replaceChildren(h("p", { class: "muted" }, "Загружаю…"));
  window.scrollTo(0, 0);
  try {
    const sum = await api("summary");
    renderNav(path === "#/" || path === "#" ? "#/" : path, { urgent: sum.urgent });
    const screens = { "": Dash, portfolio: Portfolio, batch: Batch, card: Card, deadlines: Deadlines, ip: Ip, templates: Templates, tpl: Tpl, settings: Settings, folders: Folders, interest: Interest, dossiers: Dossiers, practice: Practice, tools: AdvancedTools };
    const fn = screens[parts[0] || ""] || Dash;
    const node = await fn(parts.slice(1), params, sum);
    if (tok === navToken) view.replaceChildren(node);
  } catch (e) {
    if (tok === navToken) view.replaceChildren(errBox(e.message));
  }
}
window.addEventListener("hashchange", route);
let rerender = () => route();

function header(title, sub, ...right) {
  return h("div", { class: "head" }, h("div", null, h("h1", null, title), sub ? h("p", { class: "sub" }, sub) : null), h("div", { class: "row" }, right));
}

// ------------------------------------------------------------------ загрузка CRM
function crmButton(label, primary) {
  const inp = h("input", { type: "file", accept: ".xlsx,.xlsm,.csv,.txt", hidden: true, onchange: async () => {
    const f = inp.files[0];
    if (!f) return;
    inp.value = "";
    try {
      const j = await uploadStream("import/upload?name=" + encodeURIComponent(f.name), f);
      const res = await watchJob(j.id);
      await route();
      if (res.state === "done") showImportResult(res.result);
      else if (res.state === "error") $("#view").prepend(errBox(res.error));
    } catch (e) { $("#view").prepend(errBox(e.message)); }
  } });
  return h("span", null, inp, h("button", { class: "btn" + (primary ? " btn-p" : ""), onclick: () => inp.click() }, label));
}
function uploadStream(path, file) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", "/api/" + path);
    x.onload = () => {
      let j = null;
      try { j = JSON.parse(x.responseText); } catch (e) { /* ignore */ }
      if (x.status >= 200 && x.status < 300) resolve(j); else reject(new Error((j && j.error) || "Ошибка " + x.status));
    };
    x.onerror = () => reject(new Error("Нет связи с программой"));
    x.send(file);
  });
}
function showImportResult(r) {
  openModal([
    h("h2", null, "Портфель загружен"),
    h("p", null, "Строк в файле: ", h("b", null, num(r.rows)), r.tables > 1 ? " (листов: " + r.tables + ")" : "", ". Договоров в файле: ", h("b", null, num(r.contracts)),
      ". Новых: ", h("b", null, num(r.added)), ", обновлено: ", h("b", null, num(r.updated)),
      r.skipped ? ". Пропущено строк без номера договора: " + num(r.skipped) : "", "."),
    r.rows > r.contracts ? h("p", { class: "small muted" }, "На один договор приходится несколько строк: они объединены, пустые ячейки не затирают уже известные данные.") : null,
    r.errors && r.errors.length ? h("p", { class: "small", style: "color:#7C4300" }, "Пропущено: " + r.errors.join("; ")) : null,
    h("p", { class: "muted small" }, "Распознанные колонки: " + (r.recognized || []).join(", ")),
    r.unmapped && r.unmapped.length ? h("p", { class: "small" }, "Не использованы (неизвестные колонки): " + r.unmapped.join(", ")) : null,
    r.missing && r.missing.length ? h("div", { class: "banner amb" }, h("div", null, h("b", null, "Не хватает колонок"), "Без них часть функций работать не будет: " + r.missing.join(", "))) : null,
    h("div", { class: "row mt" }, h("button", { class: "btn btn-p", onclick: closeModal }, "Понятно")),
  ]);
}

// ------------------------------------------------------------------ Сводка
const SIT = { closed: ["Исполнено / погашено", "teal"], stop: ["Работа остановлена", "gray"], ip_filed: ["Подано заявление об ИП", "blue"], death: ["Должник умер", "red"], bankr: ["Банкротство", "red"], liq: ["Организация ликвидирована", "red"], ip_end: ["ИП окончено", "amb"], ip: ["Идёт исполнительное производство", "blue"], court: ["Есть решение суда", "blue"], expired: ["Срок давности истёк", "red"], pre: ["Досудебная работа", "gray"] };

async function Dash(_, __, s) {
  if (!s.total) {
    return h("div", null, header("Добро пожаловать", "Начните с загрузки списка договоров из CRM"),
      h("div", { class: "card" }, h("div", { class: "empty" }, h("b", null, "Портфель пока пуст"),
        "Выгрузите из CRM таблицу должников (Excel или CSV) и загрузите её сюда. Нужны хотя бы колонки: номер договора, ФИО, дата рождения, ИНН, сумма долга, дата последнего платежа, рабочий статус.",
        h("div", { class: "row mt", style: "justify-content:center" }, crmButton("Загрузить портфель из CRM", true)))));
  }
  const sitBars = Object.entries(s.sit).sort((a, b) => b[1] - a[1]).map(([k, n]) => {
    const [label, cls] = SIT[k] || [k, "gray"];
    return h("a", { class: "cand", href: "#/portfolio?sit=" + k }, h("span", null, chip(label, cls)), h("b", null, num(n)));
  });
  return h("div", null,
    header("Сводка", "Сегодня " + fd(s.today) + ". Что горит и куда смотреть в первую очередь.", crmButton("Обновить портфель из CRM")),
    h("div", { class: "grid g4" },
      h("div", { class: "card kpi" }, h("div", { class: "l" }, "Договоров в портфеле"), h("div", { class: "n" }, num(s.total)), h("div", { class: "s" }, "разобрано по документам: " + num(s.analysed))),
      h("a", { class: "card kpi", href: "#/deadlines?period=" + s.urgent_days }, h("div", { class: "l" }, "Срочно: сроки в ближайшие " + s.urgent_days + " дней"), h("div", { class: "n", style: "color:#9B2A1E" }, num(s.urgent)), h("div", { class: "s" }, "договоров, где ещё можно успеть")),
      h("a", { class: "card kpi", href: "#/ip" }, h("div", { class: "l" }, "ИП без движения"), h("div", { class: "n", style: "color:#7C4300" }, num(s.stalled)), h("div", { class: "s" }, "из " + num(s.ip_active) + " действующих ИП")),
      h("a", { class: "card kpi", href: "#/portfolio?sit=passed" }, h("div", { class: "l" }, "Сроки уже прошли"), h("div", { class: "n", style: "color:#465266" }, num(s.overdue)), h("div", { class: "s" }, "проверить, что сделано; не срочно"))),
    s.kinds && s.kinds.length ? h("div", { class: "card mb" }, h("h2", null, "Сроки по видам"),
      h("div", { class: "scroll" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, h("th", null, "Вид срока"), h("th", { class: "r" }, "В ближайшие " + s.urgent_days + " дней"), h("th", { class: "r" }, "Уже прошли"), h("th", { class: "r" }, "Всего"))),
        h("tbody", null, s.kinds.map((k) => h("tr", null,
          h("td", null, DL_TITLES[k.kind] || k.name),
          h("td", { class: "r" }, k.soon ? h("a", { class: "link", href: "#/deadlines?period=" + s.urgent_days + "&kind=" + k.kind }, num(k.soon)) : "0"),
          h("td", { class: "r" }, k.passed ? h("a", { class: "link", style: "color:#465266", href: "#/deadlines?period=overdue&kind=" + k.kind }, num(k.passed)) : "0"),
          h("td", { class: "r" }, num(k.total))))))),
      h("p", { class: "small muted mt" }, "Срок по наследству считается условно (3 года с даты смерти), поэтому у умерших должников он часто уже «прошёл»: это сигнал проверить, а не просрочка.")) : null,
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h2", null, "Ближайшие сроки"),
        s.next.length ? s.next.map((r) => h("div", { class: "row sp", style: "padding:6px 0;border-top:1px solid #EBEEF3" },
          h("div", null, h("a", { class: "link", href: "#/card/" + r.id }, r.number), h("span", { class: "muted" }, " · " + r.fio), h("div", { class: "small muted" }, r.next_dl_name)),
          dlChip(r.dl_days, r.next_dl_date))) : h("p", { class: "muted" }, "Сроков впереди нет.")),
      h("div", { class: "card" }, h("h2", null, "Ситуация по договорам"), h("div", null, sitBars))),
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h2", null, "Свежесть разбора документов"),
        h("div", { class: "row mt" },
          h("a", { class: "fchip", href: "#/portfolio?fresh=fresh" }, "Актуально (до " + s.params.fresh_days + " дн.) ", h("b", null, num(s.fresh))),
          h("a", { class: "fchip", href: "#/portfolio?fresh=stale" }, "Устарело (старше " + s.params.stale_days + " дн.) ", h("b", null, num(s.stale))),
          h("a", { class: "fchip", href: "#/portfolio?fresh=none" }, "Не разбирались ", h("b", null, num(s.total - s.analysed)))),
        h("p", { class: "muted small mt" }, "Если по договору документы разбирали недавно, повторно загружать их не нужно: откройте карточку.")),
      h("div", { class: "card" }, h("h2", null, "Последние загрузки документов"),
        s.batches.length ? s.batches.map((b) => h("div", { class: "row sp", style: "padding:6px 0;border-top:1px solid #EBEEF3" },
          h("a", { class: "link", href: "#/batch/" + b.id }, b.created.replace("T", " ").slice(0, 16)), h("span", { class: "muted" }, b.total + " " + plural(b.total, "файл", "файла", "файлов") + " · записано " + b.counts.written))) : h("p", { class: "muted" }, "Пока ничего не загружали."),
        h("div", { class: "mt" }, h("a", { class: "btn btn-p", href: "#/batch" }, "Загрузить документы")))));
}

// ------------------------------------------------------------------ Портфель
const SIT_SHORT = { ip: "Идёт ИП", ip_end: "ИП окончено", court: "Есть решение", pre: "Досудебная", expired: "Давность истекла", death: "Умер", bankr: "Банкрот", liq: "Ликвидирована", closed: "Исполнено", stop: "СТОП", ip_filed: "Подано на ИП" };
const PF_FILTERS = [["", "Все"], ["urgent", "Срочные сроки"], ["passed", "Сроки прошли"], ["stalled", "ИП без движения"], ["death", "Умер"], ["bankr", "Банкротство"], ["ip_end", "ИП окончено"], ["ip", "Идёт ИП"], ["ip_filed", "Подано на ИП"], ["court", "Есть решение"], ["expired", "Давность истекла"], ["pre", "Досудебная"], ["closed", "Исполнено"], ["stop", "СТОП"]];

async function Portfolio(_, params) {
  if (params.has("sit")) ui.pf = { q: "", sit: params.get("sit"), fresh: "", page: 1 };
  if (params.has("fresh")) ui.pf = { q: "", sit: "", fresh: params.get("fresh"), page: 1 };
  const pf = ui.pf;
  const holder = h("div");
  const input = h("input", { class: "fld", placeholder: "Поиск: номер договора, ФИО или ИНН", value: pf.q, style: "max-width:380px", oninput: debounce(() => { pf.q = input.value; pf.page = 1; load(); }, 300) });
  async function load() {
    try {
      const qs = new URLSearchParams({ q: pf.q, sit: pf.sit, fresh: pf.fresh, page: pf.page, size: 50 });
      const r = await api("contracts?" + qs);
      holder.replaceChildren(table(r));
    } catch (e) { holder.replaceChildren(errBox(e.message)); }
  }
  function table(r) {
    if (!r.rows.length) return h("div", { class: "empty" }, h("b", null, "Ничего не найдено"), "Измените поиск или фильтр.");
    const pages = Math.ceil(r.total / 50);
    return h("div", null,
      h("div", { class: "scroll" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["Договор", "Должник", "Сумма долга", "Ситуация", "Что делать", "Ближайший срок", "Разбор документов"].map((t) => h("th", null, t)))),
        h("tbody", null, r.rows.map((c) => h("tr", { class: "clk", onclick: () => (location.hash = "#/card/" + c.id) },
          h("td", null, h("a", { class: "link", href: "#/card/" + c.id, onclick: (e) => e.stopPropagation() }, c.number)),
          h("td", null, c.fio || "—", c.ip_stalled ? h("div", null, chip("ИП без движения", "amb")) : null),
          h("td", { class: "r" }, c.debt != null ? money(c.debt) : c.debt_total != null ? [money(c.debt_total), h("div", { class: "small muted" }, "с госпошлиной")] : "—"),
          h("td", null, chip(SIT_SHORT[c.sit] || c.sit_label, c.sit_cls)),
          h("td", { class: "small", style: "min-width:220px" }, c.next_action ? [h("span", { class: "dot " + (["red", "amb", "blue", "gray"][c.next_action_level] || "gray") }), c.next_action] : "—"),
          h("td", null, c.next_dl_name ? [h("div", null, c.next_dl_name), dlChip(c.dl_days, c.next_dl_date)] : "—"),
          h("td", null, freshChip(c.fresh), c.fresh.by ? h("div", { class: "small muted" }, c.fresh.by) : null)))))),
      h("div", { class: "row sp pad" }, h("span", { class: "muted" }, "Найдено: " + num(r.total)),
        pages > 1 ? h("div", { class: "row" },
          h("button", { class: "btn btn-s", disabled: pf.page <= 1, onclick: () => { pf.page--; load(); } }, "← Назад"),
          h("span", { class: "muted" }, "стр. " + pf.page + " из " + num(pages)),
          h("button", { class: "btn btn-s", disabled: pf.page >= pages, onclick: () => { pf.page++; load(); } }, "Дальше →")) : null));
  }
  const chips = h("div", { class: "row mb" });
  function drawChips() {
    chips.replaceChildren(...PF_FILTERS.map(([k, label]) => h("button", { class: "fchip" + (pf.sit === k ? " on" : ""), onclick: () => { pf.sit = k; pf.page = 1; drawChips(); load(); } }, label)),
      ...[["", "Любой разбор"], ["fresh", "Разобрано недавно"], ["stale", "Устарело"], ["none", "Не разбирался"]].map(([k, label]) =>
        h("button", { class: "fchip" + (pf.fresh === k ? " on" : ""), onclick: () => { pf.fresh = k; pf.page = 1; drawChips(); load(); } }, label)));
  }
  drawChips();
  load();
  return h("div", null,
    header("Портфель", "Все договоры из CRM. Для каждого видно ситуацию, ближайший срок и свежесть разбора документов.", crmButton("Загрузить выгрузку из CRM")),
    h("div", { class: "mb" }, input), chips, h("div", { class: "card flat" }, holder));
}

// ------------------------------------------------------------------ Карточка
function freshBanner(f) {
  const by = f.by ? " (" + f.by + ")" : "";
  if (f.level === "none") return h("div", { class: "banner gray" }, h("div", null, h("b", null, "Документы по договору ещё не разбирались"), "Загрузите документы на экране «Загрузка документов»."));
  if (f.level === "fresh") return h("div", { class: "banner teal" }, h("div", null, h("b", null, "Разбор актуален: " + f.ago + by), "Данные свежие. Повторно загружать документы не нужно."));
  if (f.level === "aging") return h("div", { class: "banner amb" }, h("div", null, h("b", null, "Разбор делали " + f.ago + by), "Проверьте, не появились ли новые документы. Если появились, загрузите их."));
  return h("div", { class: "banner red" }, h("div", null, h("b", null, "Разбор устарел: " + f.ago + by), "Прошло больше " + f.days + " дн. Загрузите свежие документы."));
}
function fieldVal(f) {
  if (f.kind === "date") return fd(f.value);
  if (f.kind === "money") return money(f.value);
  return String(f.value);
}

function actionsBlock(d) {
  const c = d.contract;
  const acts = d.actions || [];
  if (!acts.length) return null;
  const [first, ...rest] = acts;
  const btn = (a, primary) => a.tpl ? h("a", { class: "btn" + (primary ? " btn-p" : " btn-s"), href: "#/tpl/" + c.id + "/" + a.tpl }, "Подготовить документ")
    : a.go === "batch" ? h("a", { class: "btn" + (primary ? " btn-p" : " btn-s"), href: "#/batch" }, "Загрузить документы") : null;
  return h("div", { class: "card mb todo" },
    h("div", { class: "row sp" }, h("h2", null, "Что делать"), h("span", { class: "small muted" }, "Подсказка программы. Решение принимает юрист.")),
    h("div", { class: "todo-main " + first.cls },
      h("div", null,
        h("div", { class: "row" }, chip(first.label, first.cls), first.when ? h("b", null, first.when) : null),
        h("div", { class: "todo-t" }, first.title),
        h("div", { class: "small muted" }, first.why)),
      btn(first, true)),
    rest.map((a, i) => h("div", { class: "todo-row" },
      h("span", { class: "todo-n" }, String(i + 2)),
      h("div", { style: "flex:1;min-width:0" },
        h("div", null, h("b", null, a.title), " ", chip(a.label, a.cls), a.when ? h("span", { class: "small muted" }, "  " + a.when) : null),
        h("div", { class: "small muted" }, a.why)),
      btn(a, false))));
}

async function Card(parts) {
  const d = await api("contract/" + parts[0]);
  const c = d.contract;
  const tplBtns = [["ip_new", "Заявление об ИП"], ["rtk", "Реестр кредиторов"], ["claim", "Иск"], ["notary", "Запрос нотариусу"]];
  const docBlocks = d.documents.map((x) => {
    const body = h("div", { hidden:true,class:"reading-host" },readingView(x.fields,x.doc_key));
    return h("div", { style: "border-top:1px solid #EBEEF3;padding:8px 0" },
      h("div", { class: "row sp" },
        h("div", null, h("b", null, x.doc_label || x.name), h("div", { class: "small muted" }, x.name + " · загрузил " + (x.loaded_by || "—") + ", " + fd(x.loaded_at))),
        h("div", { class: "row" },
          h("button", { class: "btn btn-s", onclick: () => { body.hidden = !body.hidden; } }, "Что найдено"),
          x.has_file ? h("a", { class: "btn btn-s", href: "/api/doc/" + x.id + "/file", target: "_blank" }, "Открыть файл") : null)),
      x.has_file && x.path ? h("div", { class: "small muted" }, "Файл: " + x.path) : null,
      x.reasons && x.reasons.length ? h("div", { class: "small", style: "color:#7C4300" }, "Проверить: " + x.reasons.join("; ")) : null,
      body);
  });
  return h("div", null,
    h("div", { class: "row mb" }, h("a", { class: "link", href: "#/portfolio" }, "← К портфелю")),
    header(c.number, c.fio + (c.kind ? " · " + c.kind : ""), chip(d.sit_label, d.sit_cls),
      null),
    freshBanner(d.fresh),
    actionsBlock(d),
    d.checks.length ? h("div", { class: "banner amb" }, h("div", null, h("b", null, "Расхождения между CRM и документами"), h("ul", { style: "margin:4px 0 0;padding-left:18px" }, d.checks.map((t) => h("li", null, t))))) : null,
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h2", null, "Сроки"),
        d.deadlines.length ? d.deadlines.map((x) => h("div", { style: "padding:8px 0;border-top:1px solid #EBEEF3" },
          h("div", { class: "row sp" }, h("b", null, x.name), dlChip(x.days, x.date)),
          h("div", { class: "small muted" }, "Дата: " + fd(x.date) + ". Правило: " + x.rule), h("div", { class: "small muted" }, "Основание: " + x.src)))
          : h("p", { class: "muted" }, "Сроков нет: не хватает данных. Загрузите документы или проверьте колонки CRM."),
        h("p", { class: "small muted mt" }, "Правила расчёта условные, их согласуют юристы. Параметры меняются в настройках."),
        h("div", { class: "row mt" }, tplBtns.map(([id, label]) => h("a", { class: "btn btn-s" + (id === d.recommend ? " btn-p" : ""), href: "#/tpl/" + c.id + "/" + id }, label)))),
      h("div", { class: "card" }, h("h2", null, "История"),
        d.events.length ? h("ul", { class: "hist" }, d.events.map((e) => h("li", null, h("span", { class: "d" }, fd(e.date)), h("span", null, e.text))))
          : h("p", { class: "muted" }, "Событий пока нет: они появятся после чтения документов."))),
    h("div", { class: "card mb" }, h("h2", null, "Документы (" + d.documents.length + ")"), d.documents.length ? docBlocks : h("p", { class: "muted" }, "Документы ещё не загружались.")),
    h("div", { class: "card" }, h("h2", null, "Данные из CRM"),
      h("div", { class: "kv" }, [{ title: "Номер договора", value: c.number }, ...d.fields.filter((f) => f.key !== "number")].map((f) =>
        h("div", null, h("span", null, f.title), h("span", null, f.kind ? fieldVal(f) : f.value))))));
}

// ------------------------------------------------------------------ Загрузка документов
async function collectEntries(dt) {
  const out = [];
  const walk = async (entry, path) => {
    if (entry.isFile) {
      await new Promise((res) => entry.file((f) => { out.push({ file: f, rel: path + f.name }); res(); }, res));
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      let batch;
      do {
        batch = await new Promise((res) => reader.readEntries(res, () => res([])));
        for (const e of batch) await walk(e, path + entry.name + "/");
      } while (batch.length);
    }
  };
  const items = [...(dt.items || [])];
  if (items.length && items[0].webkitGetAsEntry) {
    const ents = items.map((i) => i.webkitGetAsEntry()).filter(Boolean);
    for (const e of ents) await walk(e, "");
  } else {
    for (const f of dt.files) out.push({ file: f, rel: f.webkitRelativePath || f.name });
  }
  return out;
}
const OK_EXT = /\.(pdf|jpe?g|png|tiff?|bmp)$/i;

async function Batch(parts) {
  if (parts[0]) return BatchView(parts[0]);
  const past = await api("batches");
  const status = h("div", { class: "mt" });
  const ocr = h("input", { type: "checkbox", checked: true, id: "ocr" });
  const drop = h("div", { class: "drop" },
    h("b", null, "Перетащите сюда документы или папку"),
    h("div", { class: "muted mb" }, "PDF и сканы (JPG, PNG, TIFF). Можно 10 файлов, можно 1000 или целую папку."),
    h("div", { class: "row", style: "justify-content:center" },
      (() => { const i = h("input", { type: "file", multiple: true, hidden: true, accept: ".pdf,.jpg,.jpeg,.png,.tif,.tiff,.bmp", onchange: () => start(h2e(i.files)) }); return h("span", null, i, h("button", { class: "btn btn-p", onclick: () => i.click() }, "Выбрать файлы")); })(),
      (() => { const i = h("input", { type: "file", hidden: true, onchange: () => start(h2e(i.files)) }); i.setAttribute("webkitdirectory", ""); return h("span", null, i, h("button", { class: "btn", onclick: () => i.click() }, "Выбрать папку")); })()),
    h("label", { class: "small muted mt", style: "display:block" }, ocr, " Распознавать сканы (медленнее, но читает и фото документов)"));
  const h2e = (fl) => [...fl].map((f) => ({ file: f, rel: f.webkitRelativePath || f.name }));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", async (e) => { e.preventDefault(); drop.classList.remove("over"); start(await collectEntries(e.dataTransfer)); });

  async function start(entries) {
    const files = entries.filter((x) => OK_EXT.test(x.file.name));
    const skipped = entries.length - files.length;
    if (!files.length) { status.replaceChildren(errBox("Подходящих файлов нет. Нужны PDF или изображения.")); return; }
    const bar = h("i", { style: "width:0" });
    const txt = h("div", { class: "small muted mt" });
    status.replaceChildren(h("div", { class: "job" }, h("b", null, "Передаю файлы в программу (они остаются на этом компьютере)"), h("div", { class: "bar mt" }, bar), txt));
    try {
      const b = await api("batch/new", {});
      let done = 0, idx = 0;
      const worker = async () => {
        while (idx < files.length) {
          const f = files[idx++];
          await uploadStream("batch/" + b.id + "/upload?path=" + encodeURIComponent(f.rel), f.file);
          done++;
          bar.style.width = Math.round((done / files.length) * 100) + "%";
          txt.textContent = done + " из " + files.length + (skipped ? " · пропущено файлов другого типа: " + skipped : "");
        }
      };
      await Promise.all([worker(), worker(), worker(), worker()]);
      const job = await api("batch/" + b.id + "/start", { ocr: ocr.checked });
      location.hash = "#/batch/" + b.id;
      void job;
    } catch (e) { status.replaceChildren(errBox(e.message)); }
  }
  return h("div", null, header("Загрузка документов", "Программа прочитает документы, найдёт договоры в портфеле и покажет, что к чему привязано. В карточки данные попадут только после вашего подтверждения."),
    h("div", { class: "card mb" }, drop, status,
      h("p", { class: "small muted mt" }, "Загруженные файлы нужны только на время чтения: после записи в карточки их копии удаляются, остаются найденные данные. Чтобы разобрать весь архив документов на диске, используйте «Подключённые папки».")),
    h("div", { class: "card" }, h("h2", null, "Прошлые загрузки"),
      past.length ? past.map((b) => h("div", { class: "row sp", style: "padding:8px 0;border-top:1px solid #EBEEF3" },
        h("a", { class: "link", href: "#/batch/" + b.id }, b.created.replace("T", " ").slice(0, 16)),
        h("span", { class: "muted" }, b.total + " " + plural(b.total, "файл", "файла", "файлов") + " · записано " + b.counts.written + " · " + ({ new: "ждёт чтения", reading: "читается", ready: "готово к проверке", written: "записано" }[b.state] || b.state)))) : h("p", { class: "muted" }, "Пока ничего не загружали.")));
}

const GROUPS = [["ok", "Привязаны", "teal"], ["confirm", "Нужно подтвердить", "amb"], ["nf", "Не найдены в портфеле", "red"], ["seen", "Уже разобраны", "gray"], ["error", "Не прочитаны", "red"], ["skip", "Пропущены", "gray"], ["written", "Записаны", "blue"]];
const groupOf = (it) => (it.written ? "written" : it.skipped ? "skip" : it.status === "seen" ? "seen" : it.status === "error" ? "error" : it.chosen != null ? "ok" : it.status === "confirm" ? "confirm" : "nf");
const bstate = { filter: "ok", limit: 200 };

function isJudgment(key){return ["decision","appeal","cassation"].includes(key)}
function readingView(fields,key){
  fields=(fields||[]).filter(f=>f.name!=="Что это за документ");
  const get=n=>fields.find(f=>f.name===n);
  const evidence=f=>h("details",{class:"reading-evidence"},h("summary",null,f.page?"Источник · стр. "+f.page:"Источник / примечание"),f.snippet?h("p",null,f.snippet):null,f.note?h("p",{class:"muted"},f.note):null);
  const datum=(label,f)=>h("div",{class:"reading-datum"},h("span",{class:"reading-label"},label),h("strong",null,f?String(f.value):"Не установлено"),f&&(f.page||f.snippet||f.note)?evidence(f):null);
  const section=(title,...kids)=>h("section",{class:"reading-section"},h("h2",null,title),kids);
  const raw=()=>h("details",{class:"reading-section"},h("summary",null,"Все извлечённые поля и источники"),h("div",{class:"reading-grid"},fields.filter(f=>f.name!=="Резолютивная часть").map(f=>datum(f.name,f))));
  if(!isJudgment(key))return h("div",{class:"reading"},section("Главное",h("div",{class:"reading-grid"},fields.filter(f=>["Дата документа","Номер дела","Номер договора","ФИО","Суд","Взыскатель / кредитор","Сумма основная","Госпошлина","Основание окончания","Номер ИП"].includes(f.name)).map(f=>datum(f.name,f)))),raw());
  const result=get("Результат рассмотрения"),operative=get("Резолютивная часть");
  const marks=fields.filter(f=>f.name.startsWith("Признак результата:"));
  const sums=fields.filter(f=>f.name.startsWith("Сумма в резолютивной части"));
  const continuing=fields.filter(f=>f.name.startsWith("Начисление до исполнения"));
  const fixed=fields.filter(f=>f.name.startsWith("Фиксированное взыскание ")), total=get("Итого распознанных фиксированных взысканий");
  const modern=!!operative;
  const historical=get("Сумма из текста (не итог взыскания)")||get("Сумма основная");
  const title= result?String(result.value):marks.some(f=>f.name.endsWith("Взыскание"))&&marks.some(f=>f.name.endsWith("Отказ"))?"Взыскание и отказ — проверить объём":marks.length?marks.map(f=>f.name.replace("Признак результата: ","")).join(" · "):"Результат не установлен";
  return h("div",{class:"reading"},
    h("div",{class:"reading-hero"},h("div",null,h("span",{class:"reading-label"},"Результат рассмотрения · предварительно"),h("h2",null,title)),chip("Требуется сверка","amb")),
    !modern?h("div",{class:"banner amb"},h("div",null,h("b",null,"Нет полного извлечённого фрагмента")," Для старого результата или нераспознанной резолютивной части нужно повторить читку оригинала.")):null,
    section("1. Реквизиты",h("div",{class:"reading-grid"},[["Дата судебного акта",get("Дата документа")],["Мотивированное решение",get("Дата мотивированного решения")],["Сопроводительное письмо",get("Дата сопроводительного письма")],["Номер дела",get("Номер дела")],["Суд",get("Суд")],["Договор",get("Номер договора")],["Должник",get("ФИО")],["Взыскатель",get("Взыскатель / кредитор")],["Вступление в силу",get("Вступил в силу")]].map(([label,f])=>datum(label,f)))),
    section("2. Денежные суммы",h("div",{class:"reading-total"},h("span",{class:"reading-label"},"Итого распознанных фиксированных взысканий"),h("strong",null,total?total.value+" ₽":"Требует подтверждения"),h("p",{class:"muted small"},total?total.note:"База будущих начислений не является отдельным взысканием. Не складывайте все найденные суммы.")),
      fixed.length?h("div",{class:"scroll"},h("table",{class:"tbl reading-money"},h("thead",null,h("tr",null,["Вид взыскания","Сумма, ₽","Источник"].map(x=>h("th",null,x)))),h("tbody",null,fixed.map(f=>h("tr",null,h("td",null,f.name.replace(/^Фиксированное взыскание \d+: /,"")),h("td",{class:"reading-amount"},f.value),h("td",null,evidence(f))))))):null,
      sums.length?h("details",{class:"reading-historical"},h("summary",null,"Все найденные суммы (включая базы и неподтверждённые)"),h("div",{class:"scroll"},h("table",{class:"tbl reading-money"},h("thead",null,h("tr",null,["Предполагаемое назначение","Сумма, ₽","Источник"].map(x=>h("th",null,x)))),h("tbody",null,sums.map(f=>{let m=f.name.match(/\(([^)]+)\)$/);return h("tr",null,h("td",null,m?m[1]:"Не определено"),h("td",{class:"reading-amount"},f.value),h("td",null,evidence(f)))}))))):h("p",{class:"muted"},"Денежные суммы в резолютивной части не распознаны."),
      historical?h("details",{class:"reading-historical"},h("summary",null,"Другие суммы из текста (не итог взыскания)"),datum("Упомянутая сумма",historical)):null),
    section("3. Продолжающиеся начисления",continuing.length?continuing.map(f=>h("div",{class:"reading-clause"},h("p",null,f.value),evidence(f))):h("p",{class:"muted"},"Условия не выделены автоматически. Проверьте резолютивную часть; это не означает, что начислений нет.")),
    section("4. Что проверить",h("ol",{class:"reading-actions"},["Сверить дату и итог решения с оригиналом.","Разделить взысканные суммы и базу дальнейших процентов; не учитывать отказанные суммы.","При наличии апелляции или кассации собрать судебное досье и установить действующий акт.","Подтвердить условия начисления и платежи перед расчётом."].map(x=>h("li",null,x)))),
    operative?h("details",{class:"reading-section"},h("summary",null,"Резолютивная часть — извлечённый текст"),h("div",{class:"reading-text"},operative.value),evidence(operative)):null,raw());
}

async function BatchView(id) {
  const holder = h("div");
  let timer = null;
  async function load() {
    const b = await api("batch/" + id);
    if (b.state === "reading" && b.source) {
      holder.replaceChildren(h("div", { class: "banner blue" }, h("div", null, h("b", null, "Папка читается"), "Ниже то, что уже требует внимания. Подтверждать можно после окончания чтения.")), results(b));
      clearTimeout(timer);
      timer = setTimeout(() => { if (location.hash === "#/batch/" + id && !document.querySelector("#modal:not([hidden])")) load().catch(() => {}); }, 4000);
      return;
    }
    if (b.state === "reading" || b.state === "new") {
      holder.replaceChildren(h("div", { class: "card" }, h("div", { class: "empty" }, h("b", null, b.state === "new" ? "Файлы загружены, чтение скоро начнётся" : "Читаю документы…"), "Прогресс виден вверху страницы. Окно можно не закрывать.")));
      clearTimeout(timer);
      timer = setTimeout(() => { if (location.hash === "#/batch/" + id) load().catch(() => {}); }, 1500);
      return;
    }
    holder.replaceChildren(results(b));
  }
  function results(b) {
    const grp = {};
    b.items.forEach((it) => { (grp[groupOf(it)] = grp[groupOf(it)] || []).push(it); });
    if (!grp[bstate.filter]) bstate.filter = ["ok", "confirm", "nf", "seen", "error", "skip", "written"].find((k) => grp[k]) || "ok";
    const all = grp[bstate.filter] || [];
    const list = all.slice(0, bstate.limit);
    const toWrite = (grp.ok || []).length;
    const contracts = new Set((grp.ok || []).map((x) => x.chosen)).size;
    return h("div", null,
      b.auto ? h("div", { class: "banner teal" }, h("div", null, h("b", null, "Записано в карточки автоматически: " + num(b.auto)), "Это документы, договор которых определён уверенно. Здесь показано только то, что требует внимания.")) : null,
      b.notes.length ? h("div", { class: "banner " + (b.source ? "gray" : "amb") }, h("div", null, h("b", null, b.source ? "Сводка" : "Замечания"), b.notes.join("; "))) : null,
      h("div", { class: "row mb" }, GROUPS.filter(([k]) => grp[k]).map(([k, label]) => h("button", { class: "fchip" + (bstate.filter === k ? " on" : ""), onclick: () => { bstate.filter = k; bstate.limit = 200; holder.replaceChildren(results(b)); } }, label, h("small", null, grp[k].length)))),
      h("div", { class: "card flat" }, list.length ? h("div", { class: "scroll" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["Файл", "Что это", "Договор", ""].map((t) => h("th", null, t)))),
        h("tbody", null, list.map((it) => itemRows(b, it))))) : h("div", { class: "empty" }, "Здесь пусто."),
        all.length > list.length ? h("div", { class: "pad" }, h("button", { class: "btn", onclick: () => { bstate.limit += 500; holder.replaceChildren(results(b)); } }, "Показать ещё (осталось " + num(all.length - list.length) + ")")) : null),
      h("div", { class: "sticky" },
        h("span", null, b.state === "written" ? "Всё записано." : "К записи: " + toWrite + " " + plural(toWrite, "документ", "документа", "документов") + " в " + contracts + " " + plural(contracts, "договор", "договора", "договоров")),
        h("div", { class: "row" },
          h("button", {class:"btn", disabled:b.state==="reading", onclick:()=>dossierDialog(b)}, "Собрать судебное досье"),
          h("a", { class: "btn", href: "#/batch" }, "Загрузить ещё"),
          h("button", { class: "btn btn-p", disabled: !toWrite, onclick: async (e) => {
            e.target.disabled = true;
            try {
              const r = await api("batch/" + id + "/write", {});
              openModal([h("h2", null, "Готово"), h("p", null, "Записано документов: " + r.documents + ", обновлено договоров: " + r.contracts + ". Сроки пересчитаны."),
                h("div", { class: "row" }, h("a", { class: "btn btn-p", href: "#/deadlines" }, "Открыть сроки"), h("button", { class: "btn", onclick: closeModal }, "Закрыть"))]);
              load();
            } catch (er) { openModal([h("h2", null, "Не записалось"), h("p", { class: "err" }, er.message), h("button", { class: "btn", onclick: closeModal }, "Закрыть")]); }
          } }, "Записать в карточки"))));
  }
  // Сверка названия файла с содержимым: понятный вывод для проверяющего
  function nameBox(it) {
    const nc = it.name_check;
    if (!nc || !nc.verdict || nc.verdict === "unknown") return null;
    if (nc.verdict === "ok") return h("div", { class: "small", style: "color:#0C5F4D;margin-top:3px" }, "✓ " + nc.title);
    const bad = nc.verdict === "wrong";
    const sym = { ok: "✓", typo: "≈", bad: "✗", info: "—" };
    const tbl = h("table", { hidden: true, class: "small", style: "margin-top:6px;border-collapse:collapse;width:100%" },
      h("tr", null, ["Что сверяли", "В названии", "В документе", ""].map((t) => h("th", { style: "text-align:left;padding:2px 8px 2px 0;color:#667" }, t))),
      (nc.rows || []).map((r) => h("tr", null,
        h("td", { style: "padding:2px 8px 2px 0" }, r.what), h("td", { style: "padding:2px 8px 2px 0" }, r.name),
        h("td", { style: "padding:2px 8px 2px 0" }, r.doc), h("td", { style: "font-weight:700;color:" + (r.state === "bad" ? "#8A1C1C" : r.state === "typo" ? "#7C4300" : "#0C5F4D") }, sym[r.state] || ""))));
    return h("div", { style: "margin-top:6px;padding:8px 10px;border-radius:8px;overflow-wrap:anywhere;background:" + (bad ? "#FDECEC" : "#FFF4E0") + ";color:" + (bad ? "#8A1C1C" : "#7C4300") },
      h("div", null, h("b", null, (bad ? "✗ " : "≈ ") + nc.title)),
      (nc.problems || []).map((p) => h("div", { class: "small" }, p)),
      nc.suggest ? h("div", { class: "small", style: "margin-top:4px" }, "Правильнее назвать: ", h("b", null, nc.suggest), " ",
        h("button", { class: "btn btn-s", onclick: async (e) => { try { await navigator.clipboard.writeText(nc.suggest); e.target.textContent = "Скопировано"; } catch (er) { e.target.textContent = "Не скопировалось"; } } }, "Копировать")) : null,
      (nc.rows || []).length ? h("div", { style: "margin-top:4px" }, h("a", { class: "link small", href: "javascript:void(0)", onclick: () => { tbl.hidden = !tbl.hidden; } }, "Что с чем сверяли")) : null,
      tbl);
  }
  function itemRows(b, it) {
    const det = h("tr", { hidden: true }, h("td", { colspan: 4 }, h("div", { class: "reading-host" }, "Загружаю…")));
    const pickable = it.status !== "error" && !it.written;
    const ch = it.chosen_info;
    const rowMain = h("tr", null,
      h("td", { style: "min-width:210px;max-width:280px;overflow-wrap:anywhere" }, it.written && !b.source ? h("b", null, it.name) : h("a", { class: "link", href: "/api/batch/" + id + "/file/" + it.idx, target: "_blank" }, it.name), it.rel !== it.name ? h("div", { class: "small muted" }, it.rel) : null,
        it.pages ? h("div", { class: "small muted" }, it.pages + " стр.") : null),
      h("td", null, it.doc_label ? chip(it.doc_label, "blue") : "—",
        isJudgment(it.doc_key) ? h("p",{class:"small muted"},"Суммы и результат — в разборе решения.") : it.summary ? h("div", { class: "small", style: "margin-top:4px" }, it.summary) : null,
        it.name_check?.verdict && it.name_check.verdict!=="ok" ? h("details",{class:"name-compact"},h("summary",null,"Сверка названия · "+it.name_check.title),nameBox(it)) : nameBox(it),
        it.reasons.filter((x) => !(it.name_check && it.name_check.verdict && x.indexOf(it.name_check.title) === 0)).length
          ? h("div", { class: "small", style: "color:#7C4300;margin-top:4px" }, "Проверить: " + it.reasons.filter((x) => !(it.name_check && it.name_check.verdict && x.indexOf(it.name_check.title) === 0)).join("; ")) : null),
      h("td", null,
        ch ? [h("a", { class: "link", href: "#/card/" + ch.id }, ch.number), h("div", { class: "small muted" }, ch.fio),
          ch.fresh && ch.fresh.level !== "none" ? h("div", { class: "small", style: ch.fresh.level === "fresh" ? "color:#0C5F4D" : "color:#7C4300" }, "Уже разбирали: " + ch.fresh.ago + (ch.fresh.by ? ", " + ch.fresh.by : "")) : null]
          : it.status === "seen" ? h("span", { class: "muted" }, it.note + (it.seen && it.seen.contract ? " (договор " + it.seen.contract + ", " + (it.seen.by || "") + " " + fd(it.seen.at) + ")" : ""))
          : it.status === "error" ? h("span", { class: "err" }, it.note)
          : it.candidates.length ? h("div", null, h("span", { class: "muted" }, "Выберите договор. Возможные:"), it.candidates.slice(0, 3).map((c) => h("div", { class: "small" }, h("b", null, c.number), " · " + (c.fio || ""), h("span", { class: "muted" }, " (" + (c.reasons || []).join(", ") + ")")))) : h("span", { class: "muted" }, "В портфеле не найден"),
        it.status === "confirm" || it.status === "nf" ? null : null),
      h("td", { class: "r" },
        h("div", { class: "row", style: "justify-content:flex-end" },
          it.nfields ? h("button", { class: "btn btn-s", onclick: async () => {
            det.hidden = !det.hidden;
            if (!det.hidden && !det.dataset.ok) {
              const r = await api("batch/" + id + "/item/" + it.idx);
              det.dataset.ok = "1";
              det.firstChild.firstChild.replaceChildren(readingView(r.fields,it.doc_key));
            }
          } }, "Разбор документа") : null,

          pickable && !it.skipped ? h("button", { class: "btn btn-s", onclick: () => pickContract(b, it) }, it.chosen != null ? "Другой договор" : "Выбрать договор") : null,
          !it.written && it.status !== "seen" ? h("button", { class: "btn btn-s", onclick: async () => { await api("batch/" + id + "/skip", { idx: it.idx, skip: !it.skipped }); load(); } }, it.skipped ? "Вернуть" : "Пропустить") : null)));
    return [rowMain, det];
  }
  function pickContract(b, it) {
    const res = h("div");
    const inp = h("input", { class: "fld", placeholder: "Номер договора, ФИО или ИНН", oninput: debounce(async () => {
      if (inp.value.trim().length < 2) return;
      const r = await api("contracts?size=8&q=" + encodeURIComponent(inp.value));
      res.replaceChildren(...r.rows.map((c) => cand(c.id, c.number, c.fio, money(c.debt), c.fresh)));
    }, 300) });
    const cand = (cid, number, fio, extra, fresh) => h("button", { class: "cand", onclick: async () => { await api("batch/" + id + "/pick", { idx: it.idx, contract_id: cid }); closeModal(); load(); } },
      h("span", null, h("b", null, number), " · " + (fio || ""), fresh && fresh.level && fresh.level !== "none" ? h("div", { class: "small muted" }, "Уже разбирали: " + fresh.ago) : null), h("span", { class: "muted" }, extra));
    openModal([h("h2", null, "К какому договору относится «" + it.name + "»?"),
      it.candidates.length ? h("div", null, h("div", { class: "small muted" }, "Программа нашла:"), it.candidates.map((c) => cand(c.id, c.number, c.fio, (c.reasons || []).join(", ")))) : null,
      h("div", { class: "small muted mt" }, "Или найдите вручную:"), inp, res,
      h("div", { class: "row mt" }, it.chosen != null ? h("button", { class: "btn", onclick: async () => { await api("batch/" + id + "/pick", { idx: it.idx, contract_id: null }); closeModal(); load(); } }, "Снять привязку") : null, h("button", { class: "btn", onclick: closeModal }, "Закрыть"))]);
  }
  await load();
  return h("div", null, header("Результат чтения", "Проверьте привязку и запишите документы в карточки договоров.", h("a", { class: "btn", href: "#/batch" }, "← К загрузке")), holder);
}

// ------------------------------------------------------------------ Подключённые папки
async function Folders() {
  const r = await api("sources");
  const msg = h("div");
  const path = h("input", { class: "fld", placeholder: "Например: S:\\Документы\\Портфель или \\\\server\\share\\Документы", style: "flex:1;min-width:280px" });
  const auto = h("input", { type: "checkbox", checked: r.auto_scan, onchange: () => api("sources/auto", { on: auto.checked }) });
  async function connect() {
    msg.replaceChildren();
    try {
      const res = await api("sources/add", { path: path.value });
      if (res.note) msg.replaceChildren(h("div", { class: "banner amb mt" }, h("div", null, res.note)));
      if (res.job) watchJob(res.job.id).then(() => { if (location.hash === "#/folders") route(); });
      route();
    } catch (e) { msg.replaceChildren(errBox(e.message)); }
  }
  const STAT = [["written", "записано", "teal"], ["confirm", "подтвердить", "amb"], ["nf", "не найдены", "red"], ["seen", "повторы", "gray"], ["error", "не прочитаны", "red"]];
  return h("div", null,
    header("Подключённые папки", "Для разбора всего архива документов. Файлы остаются на своём месте и никуда не копируются."),
    h("div", { class: "card mb" }, h("h2", null, "Подключить папку"),
      h("p", { class: "small muted" }, "Укажите папку или сетевой диск с документами. Программа прочитает все PDF и сканы во вложенных папках в фоне, уверенные привязки сразу запишет в карточки, остальное покажет для проверки. Уже прочитанные файлы повторно не читаются: при следующей проверке берутся только новые и изменённые."),
      h("div", { class: "row" }, path,
        h("button", { class: "btn", onclick: async () => { const x = await api("sources/pick", {}); if (x.path) path.value = x.path; } }, "Обзор…"),
        h("button", { class: "btn btn-p", onclick: connect }, "Подключить и прочитать")),
      msg,
      h("label", { class: "small muted mt", style: "display:block" }, auto, " Проверять новые файлы при каждом запуске программы")),
    h("div", { class: "card" }, h("h2", null, "Подключено"),
      r.sources.length ? r.sources.map((s) => h("div", { style: "padding:12px 0;border-top:1px solid #EBEEF3" },
        h("div", { class: "row sp" },
          h("div", null, h("b", null, s.path), s.available ? null : [" ", chip("недоступна", "red")],
            h("div", { class: "small muted" }, "Файлов учтено: " + num(s.files) + (s.last_scan ? " · последняя проверка " + fd(s.last_scan) + " " + s.last_scan.slice(11, 16) : " · ещё не проверялась"))),
          h("div", { class: "row" },
            s.batch_id ? h("a", { class: "btn btn-s", href: "#/batch/" + s.batch_id }, "Что требует внимания") : null,
            h("button", { class: "btn btn-s btn-p", disabled: !s.available, onclick: async (e) => { e.target.disabled = true; try { const j = await api("sources/" + s.id + "/scan", {}); watchJob(j.id).then(() => { if (location.hash === "#/folders") route(); }); } catch (er) { msg.replaceChildren(errBox(er.message)); } } }, "Проверить новые файлы"),
            h("button", { class: "btn btn-s", onclick: async () => { if (!confirmBox()) return; await api("sources/" + s.id + "/remove", {}); route(); } }, "Отключить"))),
        h("div", { class: "row mt" }, STAT.filter(([k]) => s.stats[k]).map(([k, l, c]) => chip(l + ": " + num(s.stats[k]), c)))))
        : h("p", { class: "muted" }, "Пока ни одной папки не подключено.")),
    h("p", { class: "small muted mt" }, "Отключение папки не удаляет ни файлы, ни уже записанные в карточки данные. Скорость: текстовый PDF читается за доли секунды, скан — несколько секунд на страницу. Большой архив может читаться часами; остановить можно в любой момент, при следующей проверке чтение продолжится с того же места."));
}
function confirmBox() { return true; }

// ------------------------------------------------------------------ Сроки
const DL_TITLES = { limit: "Исковая давность", reoffer: "Повторное предъявление листа", present: "Предъявление листа", rtk: "Реестр кредиторов (банкротство)", estate: "Наследство (условно)", check: "Плановая проверка ИП" };
const DL_KINDS = [["", "Все"], ["limit", "Исковая давность"], ["reoffer", "Повторное предъявление"], ["present", "Предъявить лист"], ["rtk", "Банкротство"], ["estate", "Наследство"], ["check", "Проверка ИП"]];
const DL_PERIODS = [["overdue", "Просрочены"], ["30", "30 дней"], ["90", "90 дней"], ["365", "Год"], ["all", "Все"]];

async function Deadlines(_, params) {
  if (params.has("period") || params.has("kind")) ui.dl = { kind: params.get("kind") || "", period: params.get("period") || "all", page: 1 };
  const dl = ui.dl;
  const r = await api("deadlines?" + new URLSearchParams({ kind: dl.kind, period: dl.period, page: dl.page }));
  const pages = Math.ceil(r.total / 100);
  const redo = () => { location.hash = "#/deadlines"; route(); };
  return h("div", null, header("Сроки", "Все рассчитанные сроки по договорам. Правила условные: у каждого срока показаны основание и расчёт."),
    h("div", { class: "row mb" }, DL_PERIODS.map(([k, l]) => h("button", { class: "fchip" + (dl.period === k ? " on" : ""), onclick: () => { dl.period = k; dl.page = 1; redo(); } }, l))),
    h("div", { class: "row mb" }, DL_KINDS.map(([k, l]) => h("button", { class: "fchip" + (dl.kind === k ? " on" : ""), onclick: () => { dl.kind = k; dl.page = 1; redo(); } }, l))),
    h("div", { class: "card flat" }, r.rows.length ? [h("div", { class: "scroll" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["Срок", "Дата", "Договор", "Основание и расчёт", "Разбор документов"].map((t) => h("th", null, t)))),
      h("tbody", null, r.rows.map((x) => h("tr", { class: "clk", onclick: () => (location.hash = "#/card/" + x.id) },
        h("td", null, h("b", null, x.name), h("div", null, dlChip(x.days, x.date))),
        h("td", null, fd(x.date)),
        h("td", null, h("a", { class: "link", href: "#/card/" + x.id, onclick: (e) => e.stopPropagation() }, x.number), h("div", { class: "small muted" }, x.fio), h("div", null, chip(x.sit_label, "gray"))),
        h("td", { class: "small" }, x.rule, h("div", { class: "muted" }, "Основание: " + x.src)),
        h("td", null, freshChip(x.fresh))))))),
      h("div", { class: "row sp pad" }, h("span", { class: "muted" }, "Всего: " + num(r.total)),
        pages > 1 ? h("div", { class: "row" },
          h("button", { class: "btn btn-s", disabled: dl.page <= 1, onclick: () => { dl.page--; route(); } }, "← Назад"), h("span", { class: "muted" }, dl.page + " из " + pages),
          h("button", { class: "btn btn-s", disabled: dl.page >= pages, onclick: () => { dl.page++; route(); } }, "Дальше →")) : null)]
      : h("div", { class: "empty" }, h("b", null, "Сроков нет"), "Либо всё в порядке, либо портфель ещё не загружен.")));
}

// ------------------------------------------------------------------ ИП
async function Ip() {
  const f = ui.ip;
  const r = await api("ip?" + new URLSearchParams({ filter: f.filter, page: f.page }));
  const tabs = [["stalled", "Без движения", r.counts.stalled], ["active", "Действующие", r.counts.active], ["ended", "Окончены", r.counts.ended]];
  return h("div", null, header("Исполнительные производства", "Без движения: нет поступлений в течение заданного срока (по умолчанию год)."),
    h("div", { class: "row mb" }, tabs.map(([k, l, n]) => h("button", { class: "fchip" + (f.filter === k ? " on" : ""), onclick: () => { f.filter = k; f.page = 1; route(); } }, l, h("small", null, num(n))))),
    h("div", { class: "card flat" }, r.rows.length ? h("div", { class: "scroll" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["Договор", "Возбуждено", "Идёт, мес.", "Окончено", "Платежи по ИП", "Ближайший срок", "Разбор документов"].map((t) => h("th", null, t)))),
      h("tbody", null, r.rows.map((x) => h("tr", { class: "clk", onclick: () => (location.hash = "#/card/" + x.id) },
        h("td", null, h("a", { class: "link", href: "#/card/" + x.id, onclick: (e) => e.stopPropagation() }, x.number), h("div", { class: "small muted" }, x.fio), x.ip_stalled ? chip("без движения", "amb") : null),
        h("td", null, fd(x.ip_start)), h("td", null, x.ip_months == null ? "—" : x.ip_months), h("td", null, fd(x.ip_end)), h("td", { class: "r" }, x.fssp_paid != null ? money(x.fssp_paid) : "—"),
        h("td", null, x.next_dl_name || "—", x.next_dl_date ? h("div", { class: "small muted" }, fd(x.next_dl_date)) : null), h("td", null, freshChip(x.fresh))))))) : h("div", { class: "empty" }, "Здесь пусто.")));
}

// ------------------------------------------------------------------ Шаблоны
async function Templates() {
  const tpls = await api("templates");
  const res = h("div");
  const inp = h("input", { class: "fld", placeholder: "Найдите договор: номер, ФИО или ИНН", style: "max-width:420px", oninput: debounce(async () => {
    if (inp.value.trim().length < 2) { res.replaceChildren(); return; }
    const r = await api("contracts?size=8&q=" + encodeURIComponent(inp.value));
    res.replaceChildren(...r.rows.map((c) => h("a", { class: "cand", href: "#/card/" + c.id }, h("span", null, h("b", null, c.number), " · " + c.fio), chip(c.sit_label, c.sit_cls))));
  }, 300) });
  return h("div", null, header("Шаблоны документов", "Данные подставляются из карточки договора. Чего не хватает, подсвечено, это можно дописать руками. Тексты шаблонов условные: их утвердят юристы."),
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h2", null, "Шаблоны"), tpls.map((t) => h("div", { style: "padding:8px 0;border-top:1px solid #EBEEF3" }, h("b", null, t.name), h("div", { class: "small muted" }, t.sub)))),
      h("div", { class: "card" }, h("h2", null, "Выберите договор"), h("p", { class: "muted small" }, "Откройте карточку договора и нажмите нужный шаблон."), inp, res)));
}

async function Tpl(parts) {
  const cid = parts[0], tid = parts[1];
  const holder = h("div");
  const card = await api("contract/" + cid);
  const tpls = await api("templates");
  async function draw() {
    const r = await api("render?" + new URLSearchParams({ cid, tpl: tid }));
    const tokenNodes = (toks) => toks.map((t) => (t.missing ? h("mark", null, t.text) : document.createTextNode(t.text)));
    const paper = h("div", { class: "paper" }, h("div", { class: "head-r" }, tokenNodes(r.head)), h("h3", null, r.title), h("div", null, tokenNodes(r.body)));
    const inputs = r.missing.map((m) => {
      const i = h("input", { class: "fld miss", placeholder: m.label, value: card.manual[m.key] || "", onchange: async () => { await api("manual", { cid: Number(cid), key: m.key, value: i.value }); draw(); } });
      return h("label", { style: "display:block;margin-bottom:8px" }, h("span", { class: "small muted" }, m.label), i);
    });
    holder.replaceChildren(h("div", { class: "grid", style: "grid-template-columns:minmax(0,1fr) 320px;align-items:start" },
      paper,
      h("div", { class: "card" }, h("h2", null, r.missing.length ? "Заполнить вручную" : "Всё заполнено"),
        (r.notes || []).map((n) => h("div", { class: "banner amb", style: "margin-bottom:10px" }, h("div", { class: "small" }, n))),
        r.missing.length ? [h("p", { class: "small muted" }, "Этих данных нет в карточке. Впишите их, и они подставятся в текст."), inputs] : h("p", { class: "small muted" }, "Все поля подставлены из карточки."),
        h("p", { class: "small muted" }, "Подставлено полей из карточки: " + r.filled + "."),
        h("a", { class: "btn btn-p", href: "/api/docx?" + new URLSearchParams({ cid, tpl: tid }) }, "Скачать Word"),
        h("p", { class: "small muted mt" }, "В файле Word незаполненные поля выделены жёлтым."))));
  }
  await draw();
  return h("div", null, h("div", { class: "row mb" }, h("a", { class: "link", href: "#/card/" + cid }, "← К карточке " + card.contract.number)),
    header(tpls.find((t) => t.id === tid)?.name || "Документ", card.contract.fio),
    h("div", { class: "tabs" }, tpls.map((t) => h("a", { class: "fchip" + (t.id === tid ? " on" : ""), href: "#/tpl/" + cid + "/" + t.id }, t.name))),
    holder);
}

// ------------------------------------------------------------------ Настройки
async function dossierDialog(b) {
  const panel=h("div",null,"Загружаю…");
  openModal([h("h2",null,"Собрать судебное досье"),panel,h("button",{class:"btn mt",onclick:closeModal},"Закрыть")]);
  try {
    const list=await api("dossiers");
    const select=h("select",{class:"fld"},h("option",{value:""},"Новое досье"),list.map(x=>h("option",{value:x.id},x.title+" · "+x.count+" док.")));
    const title=h("input",{class:"fld",placeholder:"ФИО / наименование должника или название дела"});
    select.addEventListener("change",()=>{title.hidden=!!select.value});
    const archive=h("input",{type:"checkbox"});
    const chosen=new Set();
    const items=b.items.filter(x=>x.status!=="error" && x.nfields);
    const err=h("div");
    panel.replaceChildren(h("p",{class:"small muted"},"Выберите только документы одного должника. Досье можно создать без загруженного портфеля CRM. Можно дополнительно сохранить оригиналы локально."),select,title,h("label",{class:"row"},archive," Сохранить копии оригиналов в локальном архиве"),
      h("div",{style:"max-height:350px;overflow:auto"},items.map(x=>h("label",{class:"row",style:"padding:6px 0"},h("input",{type:"checkbox",onchange:e=>e.target.checked?chosen.add(x.idx):chosen.delete(x.idx)}),h("span",null,x.name+" · "+x.doc_label)))),
      h("button",{class:"btn btn-p mt",onclick:async(e)=>{
        e.target.disabled=true;
        try {
          const r=await api("dossiers/attach",{batch:b.id,indices:[...chosen],title:title.value,dossier_id:select.value?Number(select.value):null,archive:archive.checked});
          closeModal();location.hash="#/dossiers/"+r.id;
        }catch(e){err.replaceChildren(errBox(e.message))}
        finally{e.target.disabled=false}
      }},"Сохранить досье"),err);
  }catch(e){panel.replaceChildren(errBox(e.message))}
}
async function Dossiers(parts) {
  if(!parts.length){
    const rows=await api("dossiers");
    return h("div",null,header("Судебные досье","Независимый от CRM архив результатов читки по должникам и судебным делам.",h("a",{class:"btn btn-p",href:"#/batch"},"Загрузить документы")),
      h("div",{class:"card"},rows.length?rows.map(x=>h("a",{class:"cand",href:"#/dossiers/"+x.id},h("b",null,x.title),h("span",null,x.count+" документов"))):h("p",{class:"muted"},"Загрузите документы и нажмите «Собрать судебное досье».")));
  }
  const d=await api("dossiers/"+parts[0]);
  const select=h("select",{class:"fld"},h("option",{value:""},"Не установлено"),d.documents.map(x=>h("option",{value:x.sha},fd(x.date)+" · "+x.doc_label+" · "+x.name)));
  select.value=d.reviewed_doc||"";
  const note=h("textarea",{class:"fld",rows:4,placeholder:"Основание: что изменено, отменено или оставлено в силе"});
  note.value=d.review_note||"";
  const msg=h("div");
  const blocks=d.documents.map(x=>{
    const fields=h("div",{class:"reading-host",hidden:true},readingView(x.fields,x.doc_key));
    return h("div",{class:"card mb"},h("div",{class:"row sp"},h("div",null,h("b",null,fd(x.date)+" · "+x.doc_label),h("p",{class:"small muted"},x.name+" · дело "+(x.case||"не установлено")+" · "+(x.court||"суд не установлен"))),
      h("div",{class:"row"},x.has_original?h("a",{class:"btn btn-s",href:"/api/dossiers/"+d.id+"/original/"+x.sha},"Скачать оригинал"):null,h("a",{class:"btn btn-s",href:"#/batch/"+x.batch},"К загрузке"),h("button",{class:"btn btn-s",onclick:()=>fields.hidden=!fields.hidden},"Реквизиты и цитаты"))),
      x.fields.find(f=>f.name==="Результат рассмотрения")?h("p",null,x.fields.find(f=>f.name==="Результат рассмотрения").value):null,
      x.reasons.length?h("p",{class:"small err"},x.reasons.join("; ")):null,fields);
  });
  return h("div",null,h("a",{class:"link",href:"#/dossiers"},"← Все досье"),
    header(d.title,d.documents.length+" документов",h("a",{class:"btn",href:"#/batch"},"Добавить документы"),h("a",{class:"btn",href:"#/interest?dossier="+d.id},"Калькулятор")),
    h("div",{class:"card mb"},h("h2",null,"Проверка судебных актов"),d.warnings.map(w=>h("p",{class:"small muted"},w)),
      h("label",null,"Акт, выбранный сотрудником после проверки",select),h("label",null,"Комментарий и основания",note),
      h("button",{class:"btn btn-p mt",onclick:async()=>{try{await api("dossiers/"+d.id+"/review",{sha:select.value,note:note.value});msg.replaceChildren(h("p",null,"Сохранено"));}catch(e){msg.replaceChildren(errBox(e.message))}}},"Сохранить заключение"),msg),
    dossierLinks(d),dossierCRM(d),dossierPayments(d),
    h("div",{class:"card mb"},h("h2",null,"История расчётов"),(d.calculations||[]).length?(d.calculations||[]).map(x=>h("div",{style:"padding:8px 0;border-top:1px solid #EBEEF3"},h("b",null,fd(x.created)+" · "+money(x.result.interest)),h("div",{class:"small muted"},fd(x.inputs.start)+" — "+fd(x.inputs.end)+" · база "+money(x.inputs.principal)),h("div",{class:"small muted"},x.source_sha&&x.source_sha===d.reviewed_doc?"Привязан к выбранному акту; платежи и условия проверить":"Требуется сверка с действующим актом"))):h("p",{class:"muted"},"Расчётов пока нет.")),
    h("h2",null,"Хронология"),blocks);
}
const RELATIONS={upheld:"Оставляет без изменения",changed:"Изменяет",cancelled:"Отменяет",remanded:"Отменяет и направляет на новое рассмотрение",enforcement:"Исполнение",related:"Связан с актом"};
function dossierLinks(d){
  const opts=()=>d.documents.map(x=>h("option",{value:x.sha},fd(x.date)+" · "+x.doc_label+" · "+x.name));
  const child=h("select",{class:"fld"},opts()),parent=h("select",{class:"fld"},opts());
  const rel=h("select",{class:"fld"},Object.entries(RELATIONS).map(([k,v])=>h("option",{value:k},v)));
  const note=h("input",{class:"fld",placeholder:"Основание и страница акта"}),msg=h("div");
  const label=sha=>d.documents.find(x=>x.sha===sha)?.name||sha;
  return h("div",{class:"card mb"},h("h2",null,"Связи судебных актов"),
    (d.links||[]).map(x=>h("p",null,h("b",null,label(x.child)), " — "+RELATIONS[x.relation]+" → "+label(x.parent),h("div",{class:"small muted"},x.note))),
    (d.suggestions||[]).map(x=>h("div",{class:"banner amb mb"},h("div",null,h("b",null,"Возможная связь: "+label(x.child)+" → "+label(x.parent)),h("p",{class:"small"},x.reason),h("button",{class:"btn btn-s",onclick:()=>{child.value=x.child;parent.value=x.parent;note.focus()}},"Проверить и заполнить связь")))),
    h("p",{class:"small muted"},"Выберите последующий акт и акт, которого он касается. Сохранение связи сбрасывает прежнее заключение для повторной проверки."),
    h("div",{class:"grid g2"},h("label",null,"Последующий акт",child),h("label",null,"Предыдущий акт",parent),rel,note),
    h("button",{class:"btn mt",disabled:d.documents.length<2,onclick:async()=>{try{await api("dossiers/"+d.id+"/link",{child:child.value,parent:parent.value,relation:rel.value,note:note.value});route()}catch(e){msg.replaceChildren(errBox(e.message))}}},"Сохранить связь"),msg);
}
function dossierPayments(d){
  const file=h("input",{type:"file",accept:".xlsx,.csv",hidden:true,onchange:async()=>{const f=file.files[0];if(!f)return;try{await uploadStream("dossiers/"+d.id+"/payment-upload?name="+encodeURIComponent(f.name),f);route()}catch(e){msg.replaceChildren(errBox(e.message))}finally{file.value=""}}});
  const input=h("textarea",{class:"fld",rows:4,placeholder:"2025-02-01;15000;10000;PAY-001"});
  const msg=h("div");
  return h("div",{class:"card mb"},h("h2",null,"Платежи"),
    h("p",{class:"small muted"},"Вставьте строки: дата ГГГГ-ММ-ДД; общая сумма; погашение базы (можно оставить пустым); уникальный номер платежа. Неизвестное распределение не используется в расчёте."),
    h("div",{class:"row mb"},file,h("button",{class:"btn",onclick:()=>file.click()},"Импорт Excel / CSV"),h("a",{class:"btn",href:"/api/dossiers/"+d.id+"/payment-template"},"Шаблон CSV")),input,h("button",{class:"btn mt",onclick:async(e)=>{e.target.disabled=true;try{
      const rows=input.value.split(/\r?\n/).filter(x=>x.trim()).map(x=>{const a=x.split(";").map(y=>y.trim());if(a.length!==4)throw new Error("Нужно четыре столбца");return {date:a[0],total:a[1],principal:a[2],reference:a[3]}});
      await api("dossiers/"+d.id+"/payments",{rows});route();
    }catch(e){msg.replaceChildren(errBox(e.message))}finally{e.target.disabled=false}}},"Добавить платежи"),msg,
    (d.payments||[]).length?h("div",{class:"scroll"},h("table",{class:"tbl"},h("thead",null,h("tr",null,["Дата","Всего","В базу","Номер"].map(x=>h("th",null,x)))),h("tbody",null,d.payments.map(x=>h("tr",null,h("td",null,fd(x.day)),h("td",null,money(x.total)),h("td",null,x.principal===null?"Не распределён":money(x.principal)," ",h("button",{class:"btn btn-s",onclick:()=>{
      const v=h("input",{class:"fld",value:x.principal??"",placeholder:"Пусто — не распределён"}),status=h("div");
      openModal([h("h2",null,"Распределение платежа "+x.reference),h("p",null,"Всего: "+money(x.total)),v,h("button",{class:"btn btn-p mt",onclick:async()=>{
        try{await api("dossiers/"+d.id+"/payments/"+x.id,{principal:v.value.trim()});closeModal();route()}catch(e){status.replaceChildren(errBox(e.message))}
      }},"Сохранить"),status]);
    }},"Уточнить")),h("td",null,x.reference)))))):null);
}
function dossierCRM(d){
  const inp=h("input",{class:"fld",type:"number",min:1,placeholder:"Внутренний ID договора"}),msg=h("div");
  return h("div",{class:"card mb"},h("h2",null,"Связь с CRM"),h("p",{class:"small muted"},"Привязка только после проверки договора и должника. Расхождения — по документам, уже записанным в карточку договора."),
    (d.crm||[]).map(x=>h("div",{class:"mb"},h("a",{class:"link",href:"#/card/"+x.id},x.number+" · "+x.fio+" (ID "+x.id+")"),h("p",null,"Остаток CRM: "+money(x.debt)),x.checks.map(y=>h("p",{class:"small err"},y)))),
    h("div",{class:"row"},inp,h("button",{class:"btn",onclick:async()=>{try{await api("dossiers/"+d.id+"/crm",{contract_id:inp.value});route()}catch(e){msg.replaceChildren(errBox(e.message))}}},"Привязать договор")),msg);
}
function lines2(v,key1,key2){
  return v.split(/\r?\n/).filter(x=>x.trim()).map(x=>{const a=x.split(";").map(y=>y.trim());if(a.length!==2||!a[0]||!a[1])throw new Error("Нужно два столбца через точку с запятой");return {[key1]:a[0],[key2]:a[1].replace(",",".")}})
}
async function AdvancedTools(){
  const b=await api("rates"),rs=h("textarea",{class:"fld",rows:6,value:b.rows.map(x=>x.date+";"+x.rate).join("\n")}),dateInput=h("input",{class:"fld",type:"date",value:b.verified||""}),source=h("input",{class:"fld",value:b.source||"",placeholder:"Источник и дата проверки"}),msg=h("div");
  const base=h("input",{class:"fld",type:"number",min:0,step:"0.01",placeholder:"Сумма, ₽"}),months=h("textarea",{class:"fld",rows:5,placeholder:"2025-01;101,2\n2025-02;100,8"}),out=h("div");
  return h("div",null,header("Справочники и расчёты","Все значения хранятся локально"),
    h("div",{class:"card mb"},h("h2",null,"Справочник ставок"),h("p",{class:"small muted"},"Введите полную проверенную историю: дата начала действия; ставка %. При сохранении справочник заменяется целиком. Программа не загружает значения из интернета."),
      rs,h("label",null,"Проверено по дату",dateInput),source,h("button",{class:"btn btn-p mt",onclick:async()=>{try{await api("rates",{rows:lines2(rs.value,"date","rate"),verified:dateInput.value,source:source.value});msg.replaceChildren(h("p",null,"Справочник сохранён"))}catch(e){msg.replaceChildren(errBox(e.message))}}},"Сохранить"),msg),
    h("div",{class:"card mb"},h("h2",null,"Индексация — математический расчёт"),h("p",{class:"small muted"},"Укажите подтверждённые последовательные полные месяцы и индексы в процентах к предыдущему месяцу. Неполные месяцы, частичные платежи и законный период определяются отдельно."),
      base,months,h("button",{class:"btn btn-p mt",onclick:async()=>{try{const r=await api("indexation",{amount:base.value,months:lines2(months.value,"month","index")});out.replaceChildren(h("h2",null,"Разница: "+money(r.difference)),h("p",null,"Индексированная сумма: "+money(r.indexed)),h("div",{class:"scroll"},h("table",{class:"tbl"},h("thead",null,h("tr",null,["Месяц","Индекс","Сумма"].map(x=>h("th",null,x)))),h("tbody",null,r.segments.map(x=>h("tr",null,h("td",null,x.month),h("td",null,x.index),h("td",null,money(x.amount))))))))}catch(e){out.replaceChildren(errBox(e.message))}}},"Рассчитать"),out));
}
async function Practice(){
  const rows=await api("practice"),title=h("input",{class:"fld",placeholder:"Название обзора"}),cat=h("input",{class:"fld",placeholder:"Категория"}),summary=h("textarea",{class:"fld",rows:8,placeholder:"Вставьте текст обзора"}),terms=h("textarea",{class:"fld",rows:3,placeholder:"ФИО, адреса, номера дел и другие сведения: каждое значение с новой строки"}),confirmed=h("input",{type:"checkbox"}),msg=h("div"),query=h("input",{class:"fld",placeholder:"Поиск по обзорам"}),results=h("div");
  const draw=()=>{let q=query.value.toLowerCase();results.replaceChildren(...rows.filter(x=>(x.title+" "+x.category+" "+x.summary).toLowerCase().includes(q)).map(x=>h("details",{class:"card mb"},h("summary",null,x.title+" · "+x.category),h("p",{style:"white-space:pre-wrap"},x.summary))))};
  query.addEventListener("input",draw);draw();
  summary.addEventListener("input",()=>confirmed.checked=false);
  title.addEventListener("input",()=>confirmed.checked=false);cat.addEventListener("input",()=>confirmed.checked=false);
  return h("div",null,header("Судебная практика","Локальная база проверенных обзоров, без автоматической публикации"),
    h("div",{class:"card mb"},h("h2",null,"Подготовить обзор"),title,cat,summary,terms,
      h("button",{class:"btn mt",onclick:async()=>{try{const r=await api("redact",{text:summary.value,terms:terms.value.split(/\r?\n/)});summary.value=r.text;confirmed.checked=false;msg.replaceChildren(h("p",null,"Черновые замены сделаны. Вручную проверьте текст, название и категорию."))}catch(e){msg.replaceChildren(errBox(e.message))}}},"Скрыть указанные сведения в тексте"),
      h("p",{class:"small muted"},"Это черновое текстовое скрытие, а не гарантированное обезличивание оригинального PDF."),
      h("label",{class:"row"},confirmed," Проверил все поля и отсутствие идентифицирующих сведений"),
      h("button",{class:"btn btn-p mt",onclick:async()=>{try{await api("practice",{title:title.value,category:cat.value,summary:summary.value,confirmed:confirmed.checked});route()}catch(e){msg.replaceChildren(errBox(e.message))}}},"Сохранить обзор"),msg),
    h("div",{class:"card mb"},query),results);
}
function backupPanel(){
  const msg=h("div");
  return h("div",{class:"card mb"},h("h2",null,"Резервная копия"),h("p",{class:"small muted"},"Снимок базы, сохранённых оригиналов и результатов чтения. Временные загрузки и подключённые сетевые папки не входят. ZIP содержит персональные данные; храните его в защищённом месте."),
    h("button",{class:"btn",onclick:async(e)=>{e.target.disabled=true;try{const r=await fetch("/api/backup",{method:"POST"});if(!r.ok){const j=await r.json();throw new Error(j.error||"Ошибка резервирования")}const url=URL.createObjectURL(await r.blob());const a=h("a",{href:url,download:"RazborDocs-backup.zip"});document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);msg.replaceChildren(h("p",null,"Архив сформирован"))}catch(e){msg.replaceChildren(errBox(e.message))}finally{e.target.disabled=false}}},"Скачать резервную копию"),msg);
}
async function Interest(parts,params) {
  const did=Number(params.get("dossier"))||null;
  const dossier=did?await api("dossiers/"+did):null;
  const field=(label,attrs={})=>{const i=h("input",{class:"fld",...attrs});return [h("label",null,h("div",{class:"small muted"},label),i),i]};
  const [lp,p]=field("База начисления, ₽",{type:"number",min:0,step:"0.01"});
  const [ls,start]=field("Начало (включительно)",{type:"date"});
  const [le,end]=field("Конец (включительно)",{type:"date"});
  const [lr,rate]=field("Ставка годовых, %",{type:"number",min:0,step:"any"});
  const [lv,verified]=field("Ставки проверены по дату",{type:"date"});
  const mode=h("select",{class:"fld"},h("option",{value:"fixed"},"Фиксированная годовая ставка"),h("option",{value:"daily"},"Фиксированная дневная ставка (неустойка)"),h("option",{value:"variable"},"Переменная годовая ставка"));
  const rates=h("textarea",{class:"fld",rows:4,placeholder:"2024-01-01;16"});
  const payments=h("textarea",{class:"fld",rows:4,placeholder:"2025-02-01;10000"});
  const variable=h("div",{hidden:true},lv,h("p",{class:"small muted"},"История ставок: дата начала действия; процент, одна строка на ставку."),rates,h("button",{class:"btn mt",onclick:async()=>{try{const b=await api("rates");if(!b.rows.length)throw new Error("Сначала заполните справочник");rates.value=b.rows.map(x=>x.date+";"+x.rate).join("\n");verified.value=b.verified;result.replaceChildren(h("p",null,"Источник: "+b.source+". Проверьте полноту истории ставок."))}catch(e){result.replaceChildren(errBox(e.message))}}},"Загрузить локальный справочник"));
  mode.addEventListener("change",()=>{variable.hidden=mode.value!=="variable";lr.hidden=mode.value==="variable";lr.firstChild.textContent=mode.value==="daily"?"Ставка за день, %":"Ставка годовых, %"});
  const result=h("div",{class:"mt"});
  const importPayments=dossier?h("button",{class:"btn mt",onclick:()=>{
    if(!start.value||!end.value){result.replaceChildren(errBox("Сначала укажите период расчёта"));return}
    const within=(dossier.payments||[]).filter(x=>x.day>=start.value&&x.day<=end.value);
    if(within.some(x=>x.principal===null)){result.replaceChildren(errBox("Есть платежи без распределения на базу. Сначала проверьте их."));return}
    payments.value=within.filter(x=>Number(x.principal)>0).map(x=>x.day+";"+x.principal).join("\n");
    result.replaceChildren(h("p",null,"Платежи перенесены: "+within.length+". Проверьте базу на дату начала и принадлежность платежей конкретному обязательству."));
  }},"Перенести платежи досье за период"):null;
  const parse=(value,key)=>value.split(/\r?\n/).filter(x=>x.trim()).map(x=>{
    const [day,v,...rest]=x.split(";").map(y=>y.trim());
    if(!day||!v||rest.length)throw new Error("Строка должна иметь формат дата;значение");
    return {date:day,[key]:v.replace(",",".")};
  });
  const calculate=h("button",{class:"btn btn-p",onclick:async(e)=>{
    e.target.disabled=true;
    result.replaceChildren();
    try{
      if(!p.value||!start.value||!end.value|| (mode.value!=="variable"&&!rate.value)) throw new Error("Заполните обязательные поля");
      const r=await api("interest",{principal:p.value,start:start.value,end:end.value,mode:mode.value,rate:rate.value,
        verified_through:verified.value,rates:mode.value==="variable"?parse(rates.value,"rate"):[],payments:parse(payments.value,"amount"),dossier_id:did});
      result.replaceChildren(h("h2",null,"Начислено: "+money(r.interest)),h("p",null,"Остаток базы: "+money(r.remaining_principal)),
        h("div",{class:"scroll"},h("table",{class:"tbl"},
          h("thead",null,h("tr",null,["Начало","Конец","Дней","База",r.rate_unit==="day"?"Ставка %/день":"Ставка %/год","Делитель","Начислено"].map(x=>h("th",null,x)))),
          h("tbody",null,r.segments.map(x=>h("tr",null,[fd(x.start),fd(x.end),x.days,money(x.base),x.rate,x.denominator,money(x.interest)].map(y=>h("td",null,y))))))),
        h("p",{class:"small muted"},r.rounding),r.saved_id?h("p",null,"Расчёт сохранён в досье"):null);
    }catch(e){result.replaceChildren(errBox(e.message))}finally{e.target.disabled=false}
  }},did?"Рассчитать и сохранить":"Рассчитать");
  return h("div",null,dossier?h("a",{class:"link",href:"#/dossiers/"+did},"← "+dossier.title):null,header("Калькулятор процентов","Годовые и дневные ставки; каждое начисление и его базу рассчитывайте отдельно."),
    h("div",{class:"card"},h("div",{class:"grid g2"},lp,ls,le,mode,lr),variable,
      h("p",{class:"small muted"},"Погашения именно базы начисления (не общая сумма платежа): дата; сумма. База уменьшается со следующего дня. Не учитываются особые ограничения, моратории, капитализация и другие исключения."),
      payments,importPayments,h("div",{class:"mt"},calculate),result));
}

async function Settings() {
  const s = await api("settings");
  const org = h("input", { class: "fld", value: s.org_name, placeholder: "Например: ООО «Коллекторское агентство»", style: "max-width:460px" });
  const inputs = {};
  const rows = Object.keys(s.titles).map((k) => {
    inputs[k] = h("input", { class: "fld", type: "number", min: 0, value: s.params[k], style: "max-width:120px" });
    return h("div", { class: "row sp", style: "padding:6px 0;border-top:1px solid #EBEEF3" }, h("span", null, s.titles[k], h("span", { class: "small muted" }, "  (по умолчанию " + s.defaults[k] + ")")), inputs[k]);
  });
  const msg = h("span", { class: "muted" });
  return h("div", null, header("Настройки", "Параметры расчёта сроков и свежести. Меняйте после согласования с юристами."),
    backupPanel(),
    h("div", { class: "card mb" }, h("h2", null, "Название организации"), h("p", { class: "small muted" }, "Подставляется в шаблоны как «взыскатель», «истец», «кредитор»."), org),
    h("div", { class: "card mb" }, h("h2", null, "Параметры сроков"), rows,
      h("div", { class: "row mt" }, h("button", { class: "btn btn-p", onclick: async () => {
        const params = {};
        for (const k in inputs) params[k] = inputs[k].value;
        await api("settings", { params, org_name: org.value, recalc: true });
        msg.textContent = "Сохранено, сроки пересчитываются.";
      } }, "Сохранить и пересчитать"), msg)),
    h("div", { class: "card mb" }, h("h2", null, "Загрузки портфеля из CRM"),
      s.imports.length ? h("table", { class: "tbl" }, h("thead", null, h("tr", null, ["Когда", "Файл", "Строк", "Добавлено", "Обновлено", "Пропущено"].map((t) => h("th", null, t)))),
        h("tbody", null, s.imports.map((i) => h("tr", null, h("td", null, i.at.replace("T", " ")), h("td", null, i.file), h("td", null, num(i.rows)), h("td", null, num(i.added)), h("td", null, num(i.updated)), h("td", null, num(i.skipped))))))
        : h("p", { class: "muted" }, "Пока не загружали."), h("div", { class: "mt" }, crmButton("Загрузить выгрузку из CRM"))),
    h("div", { class: "card" }, h("h2", null, "О программе"),
      h("p", { class: "small" }, "Версия: " + s.version), h("p", { class: "small" }, "Данные хранятся здесь: " + s.data_dir),
      h("p", { class: "small muted" }, "Чтобы закрыть программу, нажмите кнопку ниже (закрытие вкладки тоже останавливает её через пару минут)."),
      h("button", { class: "btn", onclick: async () => { try { await api("quit", {}); } catch (e) { /* закрылась */ } document.body.replaceChildren(h("div", { style: "padding:60px;text-align:center" }, h("h1", null, "Программа закрыта"), h("p", { class: "muted" }, "Окно можно закрыть. Чтобы открыть снова, запустите RazborDocs.exe."))); } }, "Закрыть программу")));
}

route();
