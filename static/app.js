const state = {
  dashboard: null,
  relationshipFilter: "not_following_back",
  search: "",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const number = new Intl.NumberFormat("it-IT");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({ error: "Risposta locale non valida." }));
  if (!response.ok || payload.error) throw new Error(payload.error || `Errore ${response.status}`);
  return payload;
}

function showError(message) {
  const banner = $("#errorBanner");
  banner.textContent = message;
  banner.classList.remove("hidden");
}

function hideError() {
  $("#errorBanner").classList.add("hidden");
}

function setBusy(busy) {
  const button = $("#refreshButton");
  button.disabled = busy;
  button.classList.toggle("loading", busy);
}

function renderDashboard(data) {
  state.dashboard = data;
  const profile = data.profile;
  const hero = $("#profileHero");
  hero.classList.remove("skeleton-block");
  hero.innerHTML = `
    <div class="hero-profile">
      <img src="${escapeHtml(profile.avatar_url)}" alt="Avatar di ${escapeHtml(profile.login)}" />
      <div class="hero-copy">
        <p class="eyebrow">Panoramica account</p>
        <h1>${escapeHtml(profile.name)}</h1>
        <p>@${escapeHtml(profile.login)}${profile.bio ? ` · ${escapeHtml(profile.bio)}` : ""}</p>
      </div>
    </div>
    <a class="hero-link" href="${escapeHtml(profile.html_url)}" target="_blank" rel="noreferrer">Apri profilo ↗</a>`;

  $("#followersCount").textContent = number.format(data.counts.followers);
  $("#followingCount").textContent = number.format(data.counts.following);
  $("#mutualCount").textContent = number.format(data.counts.mutual);
  $("#notBackCount").textContent = number.format(data.counts.not_following_back);

  $$("#relationshipFilters .filter").forEach((button) => {
    const key = button.dataset.filter;
    button.querySelector("span").textContent = number.format(data.counts[key]);
  });
  renderRelationships();
  renderRepositories(data.repositories);
  $("#lastUpdated").textContent = `Aggiornato ${formatDateTime(data.collected_at)}`;
}

function relationshipCaption(key) {
  return {
    mutual: "Vi seguite a vicenda",
    not_following_back: "Non ti segue",
    followers_not_followed: "Non lo segui",
    followers: "Ti segue",
    following: "Lo segui",
  }[key] || "Profilo GitHub";
}

function renderRelationships() {
  if (!state.dashboard) return;
  const items = state.dashboard.relationships[state.relationshipFilter] || [];
  const query = state.search.trim().toLocaleLowerCase("it");
  const filtered = items.filter((item) => item.login.toLocaleLowerCase("it").includes(query));
  $("#relationshipResultCount").textContent = `${number.format(filtered.length)} ${filtered.length === 1 ? "profilo" : "profili"}`;
  const container = $("#relationshipList");
  if (!filtered.length) {
    container.innerHTML = `<div class="no-results">Nessun profilo trovato.</div>`;
    return;
  }
  container.innerHTML = filtered.map((item) => `
    <a class="user-card" href="${escapeHtml(item.html_url)}" target="_blank" rel="noreferrer">
      <img src="${escapeHtml(item.avatar_url)}" alt="" loading="lazy" />
      <span class="user-copy">
        <strong>${escapeHtml(item.login)}</strong>
        <small>${relationshipCaption(state.relationshipFilter)}</small>
      </span>
      <span class="arrow">↗</span>
    </a>`).join("");
}

function renderRepositories(repositories) {
  const select = $("#repoSelect");
  const currentSelection = select.value;
  select.disabled = !repositories.length;
  select.innerHTML = `<option value="">Seleziona un repository…</option>` + repositories.map((repo) =>
    `<option value="${escapeHtml(repo.full_name)}">${repo.private ? "● " : ""}${escapeHtml(repo.name)}</option>`
  ).join("");
  if (repositories.some((repo) => repo.full_name === currentSelection)) {
    select.value = currentSelection;
  }
}

async function loadDashboard(refresh = false) {
  setBusy(true);
  hideError();
  try {
    const data = await api(`/api/dashboard${refresh ? "?refresh=1" : ""}`);
    renderDashboard(data);
    const selectedRepo = $("#repoSelect").value;
    if (refresh && selectedRepo) await loadTraffic(selectedRepo, true);
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function loadTraffic(repo, refresh = false) {
  if (!repo) {
    $("#trafficEmpty").classList.remove("hidden");
    $("#trafficContent").classList.add("hidden");
    return;
  }
  $("#trafficEmpty").classList.remove("hidden");
  $("#trafficEmpty").innerHTML = `<span class="empty-icon">⌁</span><h3>Leggo il traffico…</h3><p>GitHub sta preparando le metriche di ${escapeHtml(repo)}.</p>`;
  $("#trafficContent").classList.add("hidden");
  hideError();
  try {
    const suffix = refresh ? "&refresh=1" : "";
    const data = await api(`/api/traffic?repo=${encodeURIComponent(repo)}${suffix}`);
    renderTraffic(data);
  } catch (error) {
    showError(error.message);
    $("#trafficEmpty").innerHTML = `<span class="empty-icon">!</span><h3>Dati non disponibili</h3><p>${escapeHtml(error.message)}</p>`;
  }
}

function renderTraffic(data) {
  $("#trafficEmpty").classList.add("hidden");
  $("#trafficContent").classList.remove("hidden");
  $("#viewsCount").textContent = number.format(data.views.count || 0);
  $("#uniqueViewsCount").textContent = number.format(data.views.uniques || 0);
  $("#clonesCount").textContent = number.format(data.clones.count || 0);
  $("#uniqueClonesCount").textContent = number.format(data.clones.uniques || 0);
  $("#chartSubtitle").textContent = `${data.history.length} giorni conservati localmente`;
  renderChart(data.history);
  renderDataList("#referrerList", data.referrers, (item) => ({
    label: item.referrer,
    count: `${number.format(item.count)} visite`,
    unique: `${number.format(item.uniques)} uniche`,
  }));
  renderDataList("#pathList", data.paths, (item) => ({
    label: item.title || item.path,
    count: `${number.format(item.count)} visite`,
    unique: `${number.format(item.uniques)} uniche`,
  }));
  if (data.partial_errors?.length) showError("Alcune metriche secondarie non sono disponibili per questo repository.");
  $("#lastUpdated").textContent = `Traffico aggiornato ${formatDateTime(data.collected_at)}`;
}

function renderDataList(selector, items, mapper) {
  const target = $(selector);
  if (!items?.length) {
    target.innerHTML = `<div class="data-empty">Nessun dato disponibile negli ultimi 14 giorni.</div>`;
    return;
  }
  target.innerHTML = items.slice(0, 10).map((item) => {
    const row = mapper(item);
    return `<div class="data-row"><strong title="${escapeHtml(row.label)}">${escapeHtml(row.label)}</strong><span>${escapeHtml(row.count)}</span><span>${escapeHtml(row.unique)}</span></div>`;
  }).join("");
}

function renderChart(rows) {
  const target = $("#trafficChart");
  const data = (rows || []).slice(-60);
  if (!data.length) {
    target.innerHTML = `<div class="data-empty">Il grafico apparirà appena GitHub registrerà del traffico.</div>`;
    return;
  }
  const width = 900, height = 230, left = 36, right = 12, top = 10, bottom = 28;
  const innerWidth = width - left - right, innerHeight = height - top - bottom;
  const maxValue = Math.max(1, ...data.flatMap((row) => [row.views, row.unique_views]));
  const x = (index) => left + (data.length === 1 ? innerWidth / 2 : (index / (data.length - 1)) * innerWidth);
  const y = (value) => top + innerHeight - (value / maxValue) * innerHeight;
  const points = (key) => data.map((row, index) => `${x(index).toFixed(1)},${y(row[key]).toFixed(1)}`).join(" ");
  const grid = [0, .25, .5, .75, 1].map((ratio) => {
    const gy = top + innerHeight * ratio;
    const label = Math.round(maxValue * (1 - ratio));
    return `<line class="chart-grid" x1="${left}" y1="${gy}" x2="${width-right}" y2="${gy}"/><text class="chart-label" x="0" y="${gy+3}">${label}</text>`;
  }).join("");
  const first = formatDay(data[0].day), last = formatDay(data[data.length - 1].day);
  target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Grafico visite repository">
    ${grid}
    <polyline class="chart-line-views" points="${points("views")}" />
    <polyline class="chart-line-unique" points="${points("unique_views")}" />
    <text class="chart-label" x="${left}" y="${height-4}">${first}</text>
    <text class="chart-label" text-anchor="end" x="${width-right}" y="${height-4}">${last}</text>
  </svg>`;
}

function formatDateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "ora" : new Intl.DateTimeFormat("it-IT", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatDay(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return new Intl.DateTimeFormat("it-IT", { day: "2-digit", month: "short" }).format(date);
}

$$(".view-tab").forEach((button) => button.addEventListener("click", () => {
  $$(".view-tab").forEach((item) => item.classList.toggle("active", item === button));
  $$(".view-panel").forEach((panel) => panel.classList.remove("active"));
  $(`#${button.dataset.view}View`).classList.add("active");
}));

$$("#relationshipFilters .filter").forEach((button) => button.addEventListener("click", () => {
  state.relationshipFilter = button.dataset.filter;
  $$("#relationshipFilters .filter").forEach((item) => item.classList.toggle("active", item === button));
  renderRelationships();
}));

$("#relationshipSearch").addEventListener("input", (event) => {
  state.search = event.target.value;
  renderRelationships();
});
$("#repoSelect").addEventListener("change", (event) => loadTraffic(event.target.value));
$("#refreshButton").addEventListener("click", () => loadDashboard(true));

loadDashboard();
