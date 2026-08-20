const state = {
  dashboard: null,
  signals: null,
  activity: null,
  relationshipFilter: "not_following_back",
  search: "",
  currentView: "overview",
  collectionWasRunning: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const number = new Intl.NumberFormat("it-IT");
const compactNumber = new Intl.NumberFormat("it-IT", { notation: "compact", maximumFractionDigits: 1 });

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
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
  $("#collectDataButton").disabled = busy;
}

function formatDateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "ora"
    : new Intl.DateTimeFormat("it-IT", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatDay(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return new Intl.DateTimeFormat("it-IT", { day: "2-digit", month: "short" }).format(date);
}

function formatDelta(value, suffix = "") {
  if (value === null || value === undefined) return '<span class="delta new">nuovo</span>';
  const numeric = Number(value);
  const tone = numeric > 0 ? "up" : numeric < 0 ? "down" : "flat";
  const prefix = numeric > 0 ? "+" : "";
  return `<span class="delta ${tone}">${prefix}${number.format(numeric)}${suffix}</span>`;
}

function renderDashboard(data) {
  state.dashboard = data;
  const profile = data.profile;
  const hero = $("#profileHero");
  const totalStars = data.repositories.reduce((sum, repo) => sum + Number(repo.stars || 0), 0);
  hero.classList.remove("skeleton-block");
  hero.innerHTML = `
    <div class="hero-profile">
      <img src="${escapeHtml(profile.avatar_url)}" alt="Avatar di ${escapeHtml(profile.login)}" />
      <div class="hero-copy">
        <p class="eyebrow">Ecosistema GitHub</p>
        <h2>${escapeHtml(profile.name)}</h2>
        <p>@${escapeHtml(profile.login)}${profile.bio ? ` · ${escapeHtml(profile.bio)}` : ""}</p>
        <div class="hero-metrics">
          <span><strong>${number.format(data.repositories.length)}</strong> repository</span>
          <span><strong>${number.format(totalStars)}</strong> stelle</span>
          <span><strong>${number.format(data.counts.followers)}</strong> follower</span>
        </div>
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
  renderMovements(data.relationship_movements || []);
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
    container.innerHTML = '<div class="no-results">Nessun profilo trovato.</div>';
    return;
  }
  container.innerHTML = filtered.map((item) => `
    <a class="user-card" href="${escapeHtml(item.html_url)}" target="_blank" rel="noreferrer">
      <img src="${escapeHtml(item.avatar_url)}" alt="" loading="lazy" />
      <span class="user-copy"><strong>${escapeHtml(item.login)}</strong><small>${relationshipCaption(state.relationshipFilter)}</small></span>
      <span class="arrow">↗</span>
    </a>`).join("");
}

function renderMovements(items) {
  const target = $("#movementList");
  if (!items.length) {
    target.innerHTML = '<div class="data-empty">Snapshot iniziale salvato. I prossimi cambiamenti appariranno qui.</div>';
    return;
  }
  const labels = {
    new_follower: ["Nuovo follower", "+"],
    lost_follower: ["Follower perso", "−"],
    started_following: ["Hai iniziato a seguire", "→"],
    stopped_following: ["Hai smesso di seguire", "←"],
  };
  target.innerHTML = items.slice(0, 20).map((item) => {
    const [label, symbol] = labels[item.event_type] || ["Movimento", "·"];
    return `<a class="movement-row ${escapeHtml(item.event_type)}" href="${escapeHtml(item.html_url)}" target="_blank" rel="noreferrer">
      <span class="movement-symbol">${symbol}</span>
      <img src="${escapeHtml(item.avatar_url)}" alt="" loading="lazy" />
      <span><strong>@${escapeHtml(item.login)}</strong><small>${label} · ${formatDateTime(item.collected_at)}</small></span>
    </a>`;
  }).join("");
}

function renderRepositories(repositories) {
  const select = $("#repoSelect");
  const currentSelection = select.value;
  select.disabled = !repositories.length;
  select.innerHTML = '<option value="">Seleziona un repository…</option>' + repositories.map((repo) =>
    `<option value="${escapeHtml(repo.full_name)}">${repo.private ? "● " : ""}${escapeHtml(repo.name)}</option>`
  ).join("");
  if (repositories.some((repo) => repo.full_name === currentSelection)) select.value = currentSelection;
  $("#repositoryMeta").textContent = `${number.format(repositories.length)} repository monitorabili · metriche aggiornate localmente`;
}

function renderSignals(data) {
  state.signals = data;
  const icons = { reach: "↗", intent: "↓", validation: "★", community: "◎" };
  $("#signalGrid").innerHTML = data.cards.map((card) => {
    const delta = Object.hasOwn(card, "delta")
      ? formatDelta(card.delta, "%")
      : formatDelta(card.delta_absolute);
    return `<article class="signal-card ${card.key}">
      <div class="signal-top"><span class="signal-icon">${icons[card.key]}</span>${delta}</div>
      <strong>${number.format(card.value)}</strong>
      <h3>${escapeHtml(card.label)}</h3>
      <p>${escapeHtml(card.unit)}</p>
    </article>`;
  }).join("");

  const badge = $("#signalBadge");
  badge.textContent = `${number.format(data.important_signals)} segnali`;
  badge.classList.toggle("active", data.important_signals > 0);
  $("#notificationCount").textContent = number.format(data.notifications.length);
  renderOverviewRanking(data.repository_ranking);
  renderRepositoryRadar(data.repository_ranking);
  renderNotifications(data.notifications);

  const followerDelta = data.relationship_delta?.followers || 0;
  $("#followersDelta").innerHTML = followerDelta
    ? `${formatDelta(followerDelta)} negli ultimi 7 giorni`
    : "stabile negli ultimi 7 giorni";
  renderCollection(data.collection || {});
}

function renderOverviewRanking(rows) {
  const target = $("#overviewRanking");
  const visible = rows.filter((repo) => repo.signal_score || repo.stars).slice(0, 5);
  if (!visible.length) {
    target.innerHTML = '<div class="data-empty">La classifica apparirà dopo la prima raccolta completa.</div>';
    return;
  }
  const maxScore = Math.max(1, ...visible.map((repo) => repo.signal_score));
  target.innerHTML = visible.map((repo, index) => `
    <button class="ranking-row" data-repo="${escapeHtml(repo.repo)}" type="button">
      <span class="rank-index">${String(index + 1).padStart(2, "0")}</span>
      <span class="rank-copy"><strong>${escapeHtml(repo.name)}</strong><small>${number.format(repo.unique_views_7d)} visitatori · ${number.format(repo.unique_clones_7d)} cloner</small></span>
      <span class="rank-bar"><i style="width:${Math.max(4, (repo.signal_score / maxScore) * 100)}%"></i></span>
      <span class="rank-score">${repo.signal_score}</span>
    </button>`).join("");
}

function renderRepositoryRadar(rows) {
  const target = $("#repositoryRadar");
  if (!rows.length) {
    target.innerHTML = '<div class="data-empty">Nessun repository disponibile.</div>';
    return;
  }
  target.innerHTML = rows.map((repo) => `
    <button class="repo-row repo-data-row" data-repo="${escapeHtml(repo.repo)}" type="button">
      <span class="repo-name"><i class="${repo.private ? "private" : ""}"></i><span><strong>${escapeHtml(repo.name)}</strong><small>${escapeHtml(repo.language || (repo.private ? "Privato" : "Pubblico"))}</small></span></span>
      <span><strong>${number.format(repo.unique_views_7d)}</strong>${formatDelta(percentage(repo.unique_views_7d, repo.previous_unique_views), "%")}</span>
      <span><strong>${number.format(repo.unique_clones_7d)}</strong><small>${repo.intent_rate === null ? "—" : `${repo.intent_rate}% indice`}</small></span>
      <span><strong>${number.format(repo.stars)}</strong>${repo.stars_delta ? formatDelta(repo.stars_delta) : "<small>stabile</small>"}</span>
      <span class="pulse-score">${repo.signal_score}</span>
    </button>`).join("");
}

function percentage(current, previous) {
  if (!previous) return current ? null : 0;
  return Math.round(((current - previous) / previous) * 1000) / 10;
}

function renderNotifications(items) {
  const target = $("#notificationList");
  if (!items.length) {
    target.innerHTML = '<div class="quiet-state"><span>✓</span><strong>Nessun segnale urgente</strong><p>Continueremo a confrontare traffico, stelle e rete.</p></div>';
    return;
  }
  target.innerHTML = items.slice(0, 10).map((item) => `
    <a class="notification-row ${escapeHtml(item.tone)}" href="${escapeHtml(item.url || "#")}" ${item.url ? 'target="_blank" rel="noreferrer"' : ""}>
      <span class="notification-dot"></span>
      <span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.detail)}${item.occurred_at ? ` · ${formatDateTime(item.occurred_at)}` : ""}</small></span>
      <i>↗</i>
    </a>`).join("");
}

async function loadDashboard(refresh = false) {
  const data = await api(`/api/dashboard${refresh ? "?refresh=1" : ""}`);
  renderDashboard(data);
  return data;
}

async function loadSignals() {
  const data = await api("/api/signals");
  renderSignals(data);
  return data;
}

async function loadCore(refresh = false) {
  hideError();
  try {
    await loadDashboard(refresh);
    await loadSignals();
  } catch (error) {
    showError(error.message);
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
    const [traffic, starResult] = await Promise.all([
      api(`/api/traffic?repo=${encodeURIComponent(repo)}${suffix}`),
      api(`/api/stars?repo=${encodeURIComponent(repo)}${suffix}`).catch((error) => ({ error: error.message, stars: [] })),
    ]);
    renderTraffic(traffic);
    renderStars(starResult);
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

function renderStars(data) {
  $("#starCount").textContent = data.error ? "Non disponibile" : `${number.format(data.count)} totali`;
  const target = $("#starTimeline");
  if (!data.stars?.length) {
    target.innerHTML = `<div class="data-empty">${escapeHtml(data.error || "Nessuna stella con data disponibile.")}</div>`;
    return;
  }
  target.innerHTML = data.stars.slice(0, 12).map((item) => `
    <a href="${escapeHtml(item.html_url)}" target="_blank" rel="noreferrer">
      <img src="${escapeHtml(item.avatar_url)}" alt="" loading="lazy" />
      <span><strong>@${escapeHtml(item.login)}</strong><small>${item.starred_at ? formatDateTime(item.starred_at) : "Data non disponibile"}</small></span>
    </a>`).join("");
}

function renderDataList(selector, items, mapper) {
  const target = $(selector);
  if (!items?.length) {
    target.innerHTML = '<div class="data-empty">Nessun dato disponibile negli ultimi 14 giorni.</div>';
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
    target.innerHTML = '<div class="data-empty">Il grafico apparirà appena GitHub registrerà del traffico.</div>';
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
  target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Grafico visite repository">
    ${grid}
    <polyline class="chart-line-views" points="${points("views")}" />
    <polyline class="chart-line-unique" points="${points("unique_views")}" />
    <text class="chart-label" x="${left}" y="${height-4}">${formatDay(data[0].day)}</text>
    <text class="chart-label" text-anchor="end" x="${width-right}" y="${height-4}">${formatDay(data[data.length - 1].day)}</text>
  </svg>`;
}

async function loadActivity(refresh = false) {
  if (state.activity && !refresh) return;
  try {
    const data = await api(`/api/activity${refresh ? "?refresh=1" : ""}`);
    state.activity = data;
    renderActivity(data);
  } catch (error) {
    showError(error.message);
  }
}

function renderActivity(data) {
  const summaryItems = [
    ["Push", data.counts.PushEvent || 0],
    ["Pull request", data.counts.PullRequestEvent || 0],
    ["Issue", data.counts.IssuesEvent || 0],
    ["Release", data.counts.ReleaseEvent || 0],
  ];
  $("#activitySummary").innerHTML = summaryItems.map(([label, value]) =>
    `<article><span>${escapeHtml(label)}</span><strong>${number.format(value)}</strong><small>eventi recenti</small></article>`
  ).join("");

  const activityTarget = $("#activityList");
  activityTarget.innerHTML = data.events.length ? data.events.slice(0, 30).map((item) => `
    <a class="activity-row" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">
      <span class="activity-icon">${item.type === "PushEvent" ? "↑" : "↗"}</span>
      <span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.detail)}</small></span>
      <time>${formatDateTime(item.created_at)}</time>
    </a>`).join("") : '<div class="data-empty">Nessuna attività pubblica recente.</div>';

  $("#achievementGrid").innerHTML = data.achievements.map((item) => {
    const progress = Math.min(100, Math.round((item.progress / item.target) * 100));
    return `<article class="achievement-row ${escapeHtml(item.status)}">
      <div class="achievement-head"><span class="achievement-mark">◆</span><span><strong>${escapeHtml(item.name)}</strong><small>Affidabilità ${escapeHtml(item.confidence)}</small></span><b>${item.progress}/${item.target}</b></div>
      <p>${escapeHtml(item.detail)}</p>
      <div class="achievement-track"><i style="width:${progress}%"></i></div>
    </article>`;
  }).join("");
  $("#achievementNote").textContent = data.achievement_note;
}

function renderCollection(collection) {
  const running = Boolean(collection.running);
  const total = Number(collection.repos_total || 0);
  const completed = Number(collection.repos_completed || 0);
  const progress = total ? Math.round((completed / total) * 100) : 0;
  const description = running
    ? `Analisi di ${collection.current_repo || "repository"} · ${completed}/${total}`
    : collection.completed_at
      ? `Ultima raccolta completata ${formatDateTime(collection.completed_at)}`
      : "La prima raccolta automatica partirà a breve.";

  $("#sidebarCollectorText").textContent = running ? `${completed}/${total} repository` : collection.completed_at ? formatDateTime(collection.completed_at) : "In attesa";
  $("#sidebarCollectorProgress").style.width = `${running ? progress : collection.completed_at ? 100 : 0}%`;
  $("#collectionDescription").textContent = description;
  $("#collectionProgress").style.width = `${running ? progress : collection.completed_at ? 100 : 0}%`;
  $("#collectionProgressText").textContent = running ? `${progress}%` : collection.completed_at ? "100%" : "—";
  $("#collectionStatusPill").textContent = running ? "In corso" : "Pronta";
  $("#collectionStatusPill").classList.toggle("running", running);
  $("#collectDataButton").disabled = running;
  $("#refreshButton").disabled = running;
  $("#refreshButton").classList.toggle("loading", running);
  $("#refreshButton").lastChild.textContent = running ? " Raccolta…" : " Raccogli ora";
}

async function pollCollection() {
  try {
    const collection = await api("/api/collection");
    renderCollection(collection);
    if (state.collectionWasRunning && !collection.running) {
      state.collectionWasRunning = false;
      await loadCore(false);
      if (state.activity) await loadActivity(true);
    } else if (collection.running) {
      state.collectionWasRunning = true;
    }
  } catch {
    // Il caricamento principale mostrerà eventuali errori; il polling resta silenzioso.
  }
}

async function startFullCollection() {
  setBusy(true);
  hideError();
  try {
    const result = await api("/api/collect", { method: "POST" });
    state.collectionWasRunning = Boolean(result.collection?.running || result.started);
    renderCollection(result.collection || {});
  } catch (error) {
    showError(error.message);
    setBusy(false);
  }
}

const pageTitles = {
  overview: "Panoramica",
  repositories: "Repository",
  network: "Rete",
  activity: "Attività",
  data: "Dati e privacy",
};

function switchView(view) {
  if (!pageTitles[view]) view = "overview";
  state.currentView = view;
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `${view}View`));
  $("#pageTitle").textContent = pageTitles[view];
  history.replaceState(null, "", `#${view}`);
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (view === "activity") loadActivity();
}

$$(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
$$("[data-go-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.goView)));
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
$("#refreshButton").addEventListener("click", startFullCollection);
$("#collectDataButton").addEventListener("click", startFullCollection);

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-repo]");
  if (!trigger) return;
  const repo = trigger.dataset.repo;
  switchView("repositories");
  $("#repoSelect").value = repo;
  loadTraffic(repo);
});

window.addEventListener("hashchange", () => {
  const view = location.hash.slice(1) || "overview";
  if (view !== state.currentView) switchView(view);
});

switchView(location.hash.slice(1) || "overview");
loadCore();
pollCollection();
setInterval(pollCollection, 5000);
