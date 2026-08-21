const state = {
  dashboard: null,
  signals: null,
  activity: null,
  insights: null,
  digest: null,
  relationshipFilter: "not_following_back",
  search: "",
  currentView: "overview",
  collectionWasRunning: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const number = new Intl.NumberFormat("en-US");
const compactNumber = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

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
  const payload = await response.json().catch(() => ({ error: "Invalid local response." }));
  if (!response.ok || payload.error) throw new Error(payload.error || `Error ${response.status}`);
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
    ? "now"
    : new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatDay(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return new Intl.DateTimeFormat("en-US", { day: "2-digit", month: "short" }).format(date);
}

function formatDelta(value, suffix = "") {
  if (value === null || value === undefined) return '<span class="delta new">new</span>';
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
      <img src="${escapeHtml(profile.avatar_url)}" alt="${escapeHtml(profile.login)} avatar" />
      <div class="hero-copy">
        <p class="eyebrow">GitHub ecosystem</p>
        <h2>${escapeHtml(profile.name)}</h2>
        <p>@${escapeHtml(profile.login)}${profile.bio ? ` · ${escapeHtml(profile.bio)}` : ""}</p>
        <div class="hero-metrics">
          <span><strong>${number.format(data.repositories.length)}</strong> repositories</span>
          <span><strong>${number.format(totalStars)}</strong> stars</span>
          <span><strong>${number.format(data.counts.followers)}</strong> followers</span>
        </div>
      </div>
    </div>
    <a class="hero-link" href="${escapeHtml(profile.html_url)}" target="_blank" rel="noreferrer">Open profile ↗</a>`;

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
  $("#lastUpdated").textContent = `Updated ${formatDateTime(data.collected_at)}`;
}

function relationshipCaption(key) {
  return {
    mutual: "You follow each other",
    not_following_back: "Does not follow you",
    followers_not_followed: "You do not follow them",
    followers: "Follows you",
    following: "You follow them",
  }[key] || "GitHub profile";
}

function renderRelationships() {
  if (!state.dashboard) return;
  const items = state.dashboard.relationships[state.relationshipFilter] || [];
  const query = state.search.trim().toLocaleLowerCase("en");
  const filtered = items.filter((item) => item.login.toLocaleLowerCase("en").includes(query));
  $("#relationshipResultCount").textContent = `${number.format(filtered.length)} ${filtered.length === 1 ? "profile" : "profiles"}`;
  const container = $("#relationshipList");
  if (!filtered.length) {
    container.innerHTML = '<div class="no-results">No profiles found.</div>';
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
    target.innerHTML = '<div class="data-empty">Initial snapshot saved. Future changes will appear here.</div>';
    return;
  }
  const labels = {
    new_follower: ["New follower", "+"],
    lost_follower: ["Follower lost", "−"],
    started_following: ["Started following", "→"],
    stopped_following: ["Stopped following", "←"],
  };
  target.innerHTML = items.slice(0, 20).map((item) => {
    const [label, symbol] = labels[item.event_type] || ["Relationship change", "·"];
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
  select.innerHTML = '<option value="">Select a repository…</option>' + repositories.map((repo) =>
    `<option value="${escapeHtml(repo.full_name)}">${repo.private ? "● " : ""}${escapeHtml(repo.name)}</option>`
  ).join("");
  if (repositories.some((repo) => repo.full_name === currentSelection)) select.value = currentSelection;
  $("#repositoryMeta").textContent = `${number.format(repositories.length)} repositories available · metrics updated locally`;
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
  badge.textContent = `${number.format(data.important_signals)} signals`;
  badge.classList.toggle("active", data.important_signals > 0);
  $("#notificationCount").textContent = number.format(data.notifications.length);
  renderOverviewRanking(data.repository_ranking);
  renderRepositoryRadar(data.repository_ranking);
  renderNotifications(data.notifications);

  const followerDelta = data.relationship_delta?.followers || 0;
  $("#followersDelta").innerHTML = followerDelta
    ? `${formatDelta(followerDelta)} in the last 7 days`
    : "stable over the last 7 days";
  renderCollection(data.collection || {});
  maybeNotifyImportantSignals(data);
}

function renderOverviewRanking(rows) {
  const target = $("#overviewRanking");
  const visible = rows.filter((repo) => repo.signal_score || repo.stars).slice(0, 5);
  if (!visible.length) {
    target.innerHTML = '<div class="data-empty">The ranking will appear after the first full collection.</div>';
    return;
  }
  const maxScore = Math.max(1, ...visible.map((repo) => repo.signal_score));
  target.innerHTML = visible.map((repo, index) => `
    <button class="ranking-row" data-repo="${escapeHtml(repo.repo)}" type="button">
      <span class="rank-index">${String(index + 1).padStart(2, "0")}</span>
      <span class="rank-copy"><strong>${escapeHtml(repo.name)}</strong><small>${number.format(repo.unique_views_7d)} visitors · ${number.format(repo.unique_clones_7d)} cloners</small></span>
      <span class="rank-bar"><i style="width:${Math.max(4, (repo.signal_score / maxScore) * 100)}%"></i></span>
      <span class="rank-score">${repo.signal_score}</span>
    </button>`).join("");
}

function renderRepositoryRadar(rows) {
  const target = $("#repositoryRadar");
  if (!rows.length) {
    target.innerHTML = '<div class="data-empty">No repositories available.</div>';
    return;
  }
  target.innerHTML = rows.map((repo) => `
    <button class="repo-row repo-data-row" data-repo="${escapeHtml(repo.repo)}" type="button">
      <span class="repo-name"><i class="${repo.private ? "private" : ""}"></i><span><strong>${escapeHtml(repo.name)}</strong><small>${escapeHtml(repo.language || (repo.private ? "Private" : "Public"))}</small></span></span>
      <span><strong>${number.format(repo.unique_views_7d)}</strong>${formatDelta(percentage(repo.unique_views_7d, repo.previous_unique_views), "%")}</span>
      <span><strong>${number.format(repo.unique_clones_7d)}</strong><small>${repo.intent_rate === null ? "—" : `${repo.intent_rate}% intent`}</small></span>
      <span><strong>${number.format(repo.stars)}</strong>${repo.stars_delta ? formatDelta(repo.stars_delta) : "<small>stable</small>"}</span>
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
    target.innerHTML = '<div class="quiet-state"><span>✓</span><strong>No urgent signals</strong><p>We will keep comparing traffic, stars and network changes.</p></div>';
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

function renderOpportunityCenter(data) {
  state.insights = data;
  const summary = data.summary || {};
  const values = [
    number.format(summary.total || 0),
    number.format(summary.high || 0),
    `${number.format(summary.health_average || 0)}/100`,
    number.format(summary.repositories_analyzed || 0),
  ];
  $$("#opportunitySummary strong").forEach((target, index) => {
    target.textContent = values[index];
  });
  $("#opportunityCount").textContent = number.format(summary.total || 0);

  const opportunities = data.opportunities || [];
  $("#opportunityList").innerHTML = opportunities.length
    ? opportunities.slice(0, 16).map((item) => `
      <a class="opportunity-row" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">
        <span class="priority-pill ${escapeHtml(item.priority)}">${escapeHtml(item.priority)}</span>
        <span class="opportunity-copy">
          <strong>${escapeHtml(item.title)}</strong>
          <small>${escapeHtml(item.detail)}</small>
          <em>${escapeHtml(item.action)}</em>
        </span>
        <span class="opportunity-metric">${escapeHtml(item.metric)}</span>
      </a>`).join("")
    : '<div class="quiet-state"><span>✓</span><strong>Portfolio in good shape</strong><p>No urgent opportunities detected.</p></div>';

  const health = data.health || [];
  $("#healthList").innerHTML = health.length
    ? health.slice(0, 10).map((item) => `
      <div class="health-row">
        <div class="health-head"><strong title="${escapeHtml(item.repo)}">${escapeHtml(item.name)}</strong><span class="health-score">${number.format(item.score)}/100</span></div>
        <small>${item.gaps?.length ? `Improve ${escapeHtml(item.gaps.join(", "))}` : "Core project information is complete"}</small>
        <div class="health-track"><i style="width:${Math.max(2, Math.min(100, Number(item.score) || 0))}%"></i></div>
      </div>`).join("")
    : '<div class="data-empty">No repository health data available.</div>';

  const repositories = data.repositories || [];
  const selects = [$("#compareRepoA"), $("#compareRepoB")];
  const previous = selects.map((select) => select.value);
  const options = repositories.map((repo) =>
    `<option value="${escapeHtml(repo.repo)}">${escapeHtml(repo.name)}</option>`
  ).join("");
  selects.forEach((select, index) => {
    select.innerHTML = options || "<option>No repositories available</option>";
    select.disabled = repositories.length < 2;
    if (repositories.some((repo) => repo.repo === previous[index])) select.value = previous[index];
  });
  if (repositories.length >= 2) {
    if (!repositories.some((repo) => repo.repo === selects[0].value)) selects[0].value = repositories[0].repo;
    if (!repositories.some((repo) => repo.repo === selects[1].value) || selects[1].value === selects[0].value) {
      selects[1].value = repositories.find((repo) => repo.repo !== selects[0].value)?.repo || repositories[1].repo;
    }
  }
  $("#compareButton").disabled = repositories.length < 2;
}

function comparisonValue(value, suffix = "") {
  return value === null || value === undefined ? "—" : `${number.format(value)}${suffix}`;
}

function renderComparison(data) {
  const repositories = data.repositories || [];
  const target = $("#comparisonResult");
  if (!repositories.length) {
    target.innerHTML = '<div class="data-empty">No comparison data available.</div>';
    return;
  }
  target.innerHTML = `<div class="comparison-grid">${repositories.map((repo) => {
    const metrics = [
      ["Unique visitors · 7d", comparisonValue(repo.unique_views_7d)],
      ["Unique cloners · 7d", comparisonValue(repo.unique_clones_7d)],
      ["Visitor growth", repo.visitor_growth === null ? (repo.unique_views_7d ? "New" : "0%") : comparisonValue(repo.visitor_growth, "%")],
      ["Clone intent", comparisonValue(repo.intent_rate, "%")],
      ["Stars gained · 7d", `${Number(repo.stars_delta || 0) > 0 ? "+" : ""}${number.format(repo.stars_delta || 0)}`],
      ["Validation rate", comparisonValue(repo.validation_rate, "%")],
    ];
    return `<article class="comparison-repo">
      <header><a href="https://github.com/${escapeHtml(repo.repo)}" target="_blank" rel="noreferrer">${escapeHtml(repo.name)}</a><strong>${number.format(repo.signal_score || 0)}</strong></header>
      ${metrics.map(([label, value]) => `<div class="comparison-metric"><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join("")}
    </article>`;
  }).join("")}</div>`;
}

async function loadComparison() {
  const repositories = [$("#compareRepoA").value, $("#compareRepoB").value].filter(Boolean);
  if (new Set(repositories).size < 2) {
    $("#comparisonResult").innerHTML = '<div class="data-empty">Choose two different repositories.</div>';
    return;
  }
  $("#compareButton").disabled = true;
  $("#comparisonResult").innerHTML = '<div class="data-empty">Comparing repository signals…</div>';
  try {
    const query = new URLSearchParams();
    repositories.forEach((repo) => query.append("repos", repo));
    renderComparison(await api(`/api/compare?${query}`));
  } catch (error) {
    showError(error.message);
    $("#comparisonResult").innerHTML = `<div class="data-empty">${escapeHtml(error.message)}</div>`;
  } finally {
    $("#compareButton").disabled = false;
  }
}

function renderDigest(data) {
  state.digest = data;
  $("#digestPeriod").textContent = `${formatDay(data.period.from)} – ${formatDay(data.period.to)}`;
  const totals = data.totals || {};
  const followerDelta = Number(data.relationship_delta?.followers || 0);
  const cards = [
    ["Visitors", totals.unique_views_7d || 0, "unique · 7d"],
    ["Cloners", totals.unique_clones_7d || 0, "unique · 7d"],
    ["Stars", `${Number(totals.stars_delta || 0) > 0 ? "+" : ""}${number.format(totals.stars_delta || 0)}`, "net change"],
    ["Followers", `${followerDelta > 0 ? "+" : ""}${number.format(followerDelta)}`, "net change"],
  ];
  const repositories = data.top_repositories || [];
  const opportunities = data.opportunities || [];
  const alerts = data.alerts || [];
  $("#digestPreview").innerHTML = `
    <div class="digest-summary">${cards.map(([label, value, note]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`).join("")}</div>
    <div class="digest-block"><h4>Top repositories</h4>
      ${repositories.length ? repositories.slice(0, 3).map((repo) => `<div class="digest-line"><span><strong>${escapeHtml(repo.name)}</strong><small>${number.format(repo.unique_views_7d)} visitors · ${number.format(repo.unique_clones_7d)} cloners</small></span><b>${number.format(repo.signal_score)}</b></div>`).join("") : '<div class="data-empty">No traffic collected yet.</div>'}
    </div>
    <div class="digest-block"><h4>Priority actions &amp; alerts</h4>
      ${opportunities.length ? opportunities.slice(0, 3).map((item) => `<div class="digest-line"><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.action)}</small></span><b>${escapeHtml(item.priority)}</b></div>`).join("") : '<div class="data-empty">No urgent opportunities.</div>'}
      ${alerts.length ? alerts.slice(0, 2).map((item) => `<div class="digest-line"><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.detail)}</small></span><b>Alert</b></div>`).join("") : ""}
    </div>`;
}

async function loadInsights(refresh = false) {
  if (state.insights && state.digest && !refresh) return;
  hideError();
  try {
    const refreshSuffix = refresh ? "?refresh=1" : "";
    const [opportunities, digest] = await Promise.all([
      api(`/api/opportunities${refreshSuffix}`),
      api(`/api/digest${refreshSuffix}`),
    ]);
    renderOpportunityCenter(opportunities);
    renderDigest(digest);
    if ((opportunities.repositories || []).length >= 2) await loadComparison();
  } catch (error) {
    showError(error.message);
  }
}

async function copyWeeklyDigest() {
  if (!state.digest?.markdown) await loadInsights();
  if (!state.digest?.markdown) return;
  const button = $("#copyDigestButton");
  try {
    await navigator.clipboard.writeText(state.digest.markdown);
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = "Copy Markdown"; }, 1800);
  } catch {
    showError("Clipboard access is unavailable. Download the Markdown file instead.");
  }
}

const DESKTOP_ALERTS_KEY = "github-pulse-desktop-alerts";
const DESKTOP_ALERTS_SEEN_KEY = "github-pulse-desktop-alerts-seen";

function alertsEnabled() {
  try {
    return localStorage.getItem(DESKTOP_ALERTS_KEY) === "enabled";
  } catch {
    return false;
  }
}

function updateDesktopAlertUI() {
  const button = $("#desktopAlertsButton");
  const status = $("#desktopAlertsStatus");
  if (!("Notification" in window)) {
    button.disabled = true;
    button.textContent = "Notifications unavailable";
    status.textContent = "This browser does not support desktop notifications.";
    return;
  }
  const enabled = alertsEnabled() && Notification.permission === "granted";
  button.textContent = enabled ? "Disable desktop alerts" : "Enable desktop alerts";
  status.textContent = enabled
    ? "Enabled · alerts stay local to this browser"
    : Notification.permission === "denied"
      ? "Blocked by the browser · update site permissions to enable"
      : "Disabled · nothing leaves your computer";
}

async function toggleDesktopAlerts() {
  if (!("Notification" in window)) return;
  try {
    if (alertsEnabled()) {
      localStorage.removeItem(DESKTOP_ALERTS_KEY);
    } else {
      const permission = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
      if (permission === "granted") localStorage.setItem(DESKTOP_ALERTS_KEY, "enabled");
    }
  } catch {
    showError("Desktop notification settings could not be updated.");
  }
  updateDesktopAlertUI();
}

function maybeNotifyImportantSignals(data) {
  if (!("Notification" in window) || !alertsEnabled() || Notification.permission !== "granted" || !data.important_signals) return;
  const signalId = data.generated_at || "";
  try {
    if (!signalId || localStorage.getItem(DESKTOP_ALERTS_SEEN_KEY) === signalId) return;
    const important = (data.notifications || []).filter((item) => ["traffic_spike", "new_stars", "new_follower"].includes(item.type));
    const detail = important.slice(0, 2).map((item) => item.title).join(" · ");
    new Notification(`GitHub Pulse · ${data.important_signals} important signal${data.important_signals === 1 ? "" : "s"}`, {
      body: detail || "Open the dashboard to review the latest changes.",
    });
    localStorage.setItem(DESKTOP_ALERTS_SEEN_KEY, signalId);
  } catch {
    // Notification delivery should never interrupt dashboard rendering.
  }
}

async function loadTraffic(repo, refresh = false) {
  if (!repo) {
    $("#trafficEmpty").classList.remove("hidden");
    $("#trafficContent").classList.add("hidden");
    return;
  }
  $("#trafficEmpty").classList.remove("hidden");
  $("#trafficEmpty").innerHTML = `<span class="empty-icon">⌁</span><h3>Reading traffic…</h3><p>GitHub is preparing metrics for ${escapeHtml(repo)}.</p>`;
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
    $("#trafficEmpty").innerHTML = `<span class="empty-icon">!</span><h3>Data unavailable</h3><p>${escapeHtml(error.message)}</p>`;
  }
}

function renderTraffic(data) {
  $("#trafficEmpty").classList.add("hidden");
  $("#trafficContent").classList.remove("hidden");
  $("#viewsCount").textContent = number.format(data.views.count || 0);
  $("#uniqueViewsCount").textContent = number.format(data.views.uniques || 0);
  $("#clonesCount").textContent = number.format(data.clones.count || 0);
  $("#uniqueClonesCount").textContent = number.format(data.clones.uniques || 0);
  $("#chartSubtitle").textContent = `${data.history.length} days stored locally`;
  renderChart(data.history);
  renderDataList("#referrerList", data.referrers, (item) => ({
    label: item.referrer,
    count: `${number.format(item.count)} views`,
    unique: `${number.format(item.uniques)} unique`,
  }));
  renderDataList("#pathList", data.paths, (item) => ({
    label: item.title || item.path,
    count: `${number.format(item.count)} views`,
    unique: `${number.format(item.uniques)} unique`,
  }));
  if (data.partial_errors?.length) showError("Some secondary metrics are unavailable for this repository.");
  $("#lastUpdated").textContent = `Traffic updated ${formatDateTime(data.collected_at)}`;
}

function renderStars(data) {
  $("#starCount").textContent = data.error ? "Unavailable" : `${number.format(data.count)} total`;
  const target = $("#starTimeline");
  if (!data.stars?.length) {
    target.innerHTML = `<div class="data-empty">${escapeHtml(data.error || "No dated stars available.")}</div>`;
    return;
  }
  target.innerHTML = data.stars.slice(0, 12).map((item) => `
    <a href="${escapeHtml(item.html_url)}" target="_blank" rel="noreferrer">
      <img src="${escapeHtml(item.avatar_url)}" alt="" loading="lazy" />
      <span><strong>@${escapeHtml(item.login)}</strong><small>${item.starred_at ? formatDateTime(item.starred_at) : "Date unavailable"}</small></span>
    </a>`).join("");
}

function renderDataList(selector, items, mapper) {
  const target = $(selector);
  if (!items?.length) {
    target.innerHTML = '<div class="data-empty">No data available in the last 14 days.</div>';
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
    target.innerHTML = '<div class="data-empty">The chart will appear as soon as GitHub records traffic.</div>';
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
  target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Repository traffic chart">
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
    `<article><span>${escapeHtml(label)}</span><strong>${number.format(value)}</strong><small>recent events</small></article>`
  ).join("");

  const activityTarget = $("#activityList");
  activityTarget.innerHTML = data.events.length ? data.events.slice(0, 30).map((item) => `
    <a class="activity-row" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">
      <span class="activity-icon">${item.type === "PushEvent" ? "↑" : "↗"}</span>
      <span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.detail)}</small></span>
      <time>${formatDateTime(item.created_at)}</time>
    </a>`).join("") : '<div class="data-empty">No recent public activity.</div>';

  $("#achievementGrid").innerHTML = data.achievements.map((item) => {
    const progress = Math.min(100, Math.round((item.progress / item.target) * 100));
    return `<article class="achievement-row ${escapeHtml(item.status)}">
      <div class="achievement-head"><span class="achievement-mark">◆</span><span><strong>${escapeHtml(item.name)}</strong><small>Confidence: ${escapeHtml(item.confidence)}</small></span><b>${item.progress}/${item.target}</b></div>
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
    ? `Analyzing ${collection.current_repo || "repository"} · ${completed}/${total}`
    : collection.completed_at
      ? `Last collection completed ${formatDateTime(collection.completed_at)}`
      : "The first automatic collection will start shortly.";

  $("#sidebarCollectorText").textContent = running ? `${completed}/${total} repositories` : collection.completed_at ? formatDateTime(collection.completed_at) : "Waiting";
  $("#sidebarCollectorProgress").style.width = `${running ? progress : collection.completed_at ? 100 : 0}%`;
  $("#collectionDescription").textContent = description;
  $("#collectionProgress").style.width = `${running ? progress : collection.completed_at ? 100 : 0}%`;
  $("#collectionProgressText").textContent = running ? `${progress}%` : collection.completed_at ? "100%" : "—";
  $("#collectionStatusPill").textContent = running ? "Running" : "Ready";
  $("#collectionStatusPill").classList.toggle("running", running);
  $("#collectDataButton").disabled = running;
  $("#refreshButton").disabled = running;
  $("#refreshButton").classList.toggle("loading", running);
  $("#refreshButton").lastChild.textContent = running ? " Collecting…" : " Collect now";
}

async function pollCollection() {
  try {
    const collection = await api("/api/collection");
    renderCollection(collection);
    if (state.collectionWasRunning && !collection.running) {
      state.collectionWasRunning = false;
      await loadCore(false);
      if (state.activity) await loadActivity(true);
      if (state.insights) await loadInsights(true);
    } else if (collection.running) {
      state.collectionWasRunning = true;
    }
  } catch {
    // The main load reports errors; polling stays silent.
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
  overview: "Overview",
  repositories: "Repositories",
  insights: "Insights",
  network: "Network",
  activity: "Activity",
  data: "Data & privacy",
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
  if (view === "insights") loadInsights();
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
$("#compareButton").addEventListener("click", loadComparison);
$("#copyDigestButton").addEventListener("click", copyWeeklyDigest);
$("#desktopAlertsButton").addEventListener("click", toggleDesktopAlerts);

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
updateDesktopAlertUI();
loadCore();
pollCollection();
setInterval(pollCollection, 5000);
