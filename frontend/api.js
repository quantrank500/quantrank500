// QuantRank500 frontend — one constant, vanilla JS, no build step (spec §9).
// Local dev talks to the API on :8000; any deployed host uses same-origin /api
// (nginx proxies it), which also removes CORS from the picture.
const LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);
const API = LOCAL ? "http://localhost:8000" : "/api";

// Per-site analytics: each host counts into its own umami website, and the
// footer's Traffic link opens that site's public dashboard. Ids and share
// URLs are public by design.
const SITE = {
  "demo.quantrank500.com": {
    umamiId: "d99eb698-021c-4b62-ba80-25baea546182",
    traffic: "https://analytics.quantrank500.com/share/qr500demo/demo.quantrank500.com",
  },
  "quantrank500.com": {
    umamiId: "485f4e95-4230-4b02-8abb-1f0ba5bd8989",
    traffic: "https://analytics.quantrank500.com/share/WGYEGt2SJxKZeaKf",
  },
}[location.hostname.replace(/^www\./, "")];

// Cookieless page counts (Cloudflare Web Analytics) on deployed hosts only —
// local browsing is not traffic. The beacon token is public by design.
if (!LOCAL) {
  const beacon = document.createElement("script");
  beacon.src = "https://static.cloudflareinsights.com/beacon.min.js";
  beacon.defer = true;
  beacon.setAttribute("data-cf-beacon",
    '{"token": "65b2eb42c9fa41509ae8f30d619b7885"}');
  document.head.appendChild(beacon);

  if (SITE) {
    const umami = document.createElement("script");
    umami.src = "https://analytics.quantrank500.com/script.js";
    umami.defer = true;
    umami.setAttribute("data-website-id", SITE.umamiId);
    document.head.appendChild(umami);
  }
}

const TRAFFIC_URL = SITE ? SITE.traffic : null;

// Ledger-tier identity: UUID + api_token live only in this browser's localStorage.
// If localStorage is lost and the key was never saved or upgraded, the record is
// unclaimable. Stated plainly in the UI (spec §3). Identities are created only on
// first post — browsing never creates one.
function storedIdentity() {
  const saved = localStorage.getItem("qr500_identity");
  return saved ? JSON.parse(saved) : null;
}

async function ensureIdentity() {
  const existing = storedIdentity();
  if (existing) return existing;
  const created = await (await fetch(`${API}/identities`, { method: "POST" })).json();
  localStorage.setItem("qr500_identity", JSON.stringify(created));
  return created;
}

// --- The identity key: save and restore (self-custody; no accounts, no recovery) ---

function identityKeyText() {
  const identity = storedIdentity();
  return JSON.stringify(
    { service: "QuantRank500 identity key",
      public_id: identity.public_id, api_token: identity.api_token }, null, 2);
}

function downloadIdentityKey() {
  const blob = new Blob([identityKeyText()], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "quantrank500-identity-key.json";
  link.click();
  URL.revokeObjectURL(link.href);
  markKeySaved();
}

async function copyIdentityKey() {
  await navigator.clipboard.writeText(identityKeyText());
  markKeySaved();
}

function markKeySaved() {
  localStorage.setItem("qr500_key_saved", "yes");
  document.querySelectorAll(".key-saved-note").forEach(el => el.textContent = "Key saved.");
}

function keySaved() {
  return localStorage.getItem("qr500_key_saved") === "yes";
}

function restoreIdentity(text) {
  const parsed = JSON.parse(text);
  if (!parsed.public_id || !parsed.api_token) {
    throw new Error("That is not a QuantRank500 identity key.");
  }
  localStorage.setItem("qr500_identity", JSON.stringify(
    { public_id: parsed.public_id, api_token: parsed.api_token }));
  markKeySaved();
}

const KEY_WARNING =
  "This key is the only proof this record is yours. It cannot be recovered, " +
  "and anyone who has it can post as you. Keep it private.";

function saveKeyControls() {
  return `
    <p>${KEY_WARNING}</p>
    <button type="button" class="small" onclick="downloadIdentityKey()">Download key</button>
    <button type="button" class="small" onclick="copyIdentityKey()">Copy key</button>
    <span class="key-saved-note note"></span>`;
}

async function postPrediction(plan) {
  const identity = await ensureIdentity();
  const response = await fetch(`${API}/predictions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Api-Token": identity.api_token },
    body: JSON.stringify(plan),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || response.statusText);
  return body;
}

async function getJson(path) {
  let response;
  try {
    response = await fetch(`${API}${path}`);
  } catch {
    throw new Error("The record is temporarily unreachable. Please try again shortly.");
  }
  if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
  return response.json();
}

// Theme: system preference by default; an explicit choice wins and persists.
const savedTheme = localStorage.getItem("qr500_theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
// ?theme=dark|light — a shareable themed link; wins for this view, never persists
const urlTheme = new URLSearchParams(location.search).get("theme");
if (urlTheme === "dark" || urlTheme === "light") {
  document.documentElement.dataset.theme = urlTheme;
}

// The info strip: three fixed lines below the nav — status, statement, signature.
// It sits OUTSIDE the sticky header and scrolls away; only the nav stays pinned.
document.addEventListener("DOMContentLoaded", () => {
  const header = document.querySelector("header.site");
  if (header) {
    const strip = document.createElement("div");
    strip.className = "info-strip";
    strip.innerHTML = `<div class="info-inner">
      <div class="strip-fresh"></div>
      <div>A record of predictions, not investment advice.</div>
      <div>Created by <a href="https://ruslandubas.com">Ruslan Dubas</a>
        · <a href="https://github.com/quantrank500">Support this project</a>
        · <a href="glossary.html">Definitions</a>
        · <a href="faq.html">FAQ</a>
        · <a href="https://github.com/quantrank500/quantrank500">Code</a>
        · <a href="${API}/docs">APIs</a>
        · <a href="privacy.html">Privacy</a>${TRAFFIC_URL
          ? ` · <a href="${TRAFFIC_URL}">Traffic</a>` : ""}</div>
    </div>`;
    header.insertAdjacentElement("afterend", strip);
  }
});

document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.createElement("button");
  toggle.className = "theme-toggle";
  toggle.textContent = "◐";
  toggle.title = "Toggle light / dark";
  toggle.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme
      || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("qr500_theme", next);
  });
  document.body.appendChild(toggle);
});

// Label every page with the serving environment (DEMO/TEST/DEV) so simulated
// data can never be mistaken for the real ledger. Production shows no badge.
document.addEventListener("DOMContentLoaded", async () => {
  try {
    const info = await getJson("/");
    if (info.environment && info.environment !== "prod") {
      const badge = document.createElement("span");
      badge.className = "env-badge";
      badge.textContent = info.environment.toUpperCase();
      document.body.appendChild(badge);  // fixed top-right; never affects layout
    }
    if (info.environment === "demo") {
      const note = document.getElementById("demo-note");
      if (note) note.hidden = false;
    }
  } catch { /* API down: no badge */ }
});

// Underline the nav tab of the page being viewed. Exception: a public profile
// (?id=...) is someone else's record — underlining "My Record" would mislabel it.
document.addEventListener("DOMContentLoaded", () => {
  const page = location.pathname.split("/").pop() || "index.html";
  const viewingSomeoneElse =
    page === "profile.html" && new URLSearchParams(location.search).has("id");
  if (viewingSomeoneElse) return;
  document.querySelectorAll("header.site nav a").forEach(link => {
    if (link.getAttribute("href") === page) link.classList.add("active");
  });
});

const escapeHtml = s => String(s).replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Deterministic identicon from the UUID: a symmetric 5x5 mark in a muted hue.
// No uploads, no storage, no moderation — the identity draws its own picture.
function identicon(id, size = 16) {
  const hex = id.replace(/-/g, "");
  const hue = parseInt(hex.slice(0, 4), 16) % 360;
  let cells = "";
  for (let row = 0; row < 5; row++) {
    for (let col = 0; col < 3; col++) {
      if (parseInt(hex[4 + row * 3 + col], 16) % 2) continue;
      cells += `<rect x="${col}" y="${row}" width="1" height="1"/>`;
      if (col < 2) cells += `<rect x="${4 - col}" y="${row}" width="1" height="1"/>`;
    }
  }
  return `<svg class="identicon" width="${size}" height="${size}"` +
    ` viewBox="-0.6 -0.6 6.2 6.2" fill="hsl(${hue} 35% 45%)" aria-hidden="true">` +
    `<rect x="-0.6" y="-0.6" width="6.2" height="6.2" fill="rgba(128,128,128,0.12)"/>` +
    `${cells}</svg>`;
}

// How an identity renders everywhere: mark + name with a short de-emphasized UUID
// suffix (unverified names are not unique, spec §3 — the suffix disambiguates and
// blocks imitation), or mark + 8-char prefix when unnamed. Full UUID in the tooltip.
function identityLabel(id, name) {
  const text = name
    ? `${escapeHtml(name)} <span class="suffix">· ${id.slice(0, 4)}</span>`
    : id.slice(0, 8);
  return `${identicon(id)}<a class="mono" href="profile.html?id=${id}"` +
    ` title="${id}">${text}</a>`;
}

// Real supporters only — the "Supported by" strip renders only when non-empty.
// Entries: { name, url }. An empty or aspirational sponsor wall is exactly the
// sketchy signal this project refuses to send.
const SUPPORTERS = [
  { name: "Ruslan Dubas", url: "https://ruslandubas.com" },
];

// The site footer: freshness line, then credit + support links, then (dormant)
// the supporters strip. Injected once here so all pages stay identical.
document.addEventListener("DOMContentLoaded", () => {
  const footer = document.querySelector("footer.freshness");
  if (!footer) return;
  // the identity sentence opens the footer — a statement, not an apology
  const identityLine = document.createElement("div");
  identityLine.className = "fresh-line";
  identityLine.textContent = "A record of predictions, not investment advice.";
  footer.textContent = "";
  footer.appendChild(identityLine);

  const seal = document.createElement("div");
  seal.className = "footer-seal";
  seal.innerHTML =
    '<svg class="mark" viewBox="0 0 64 64" aria-hidden="true">' +
    '<rect width="64" height="64" rx="14" style="fill:var(--ink,#202124)"/>' +
    '<g transform="translate(18.351,44) scale(0.017578,-0.017578)">' +
    '<path style="fill:var(--bg,#ffffff)" d="M770 -25Q462 -25 268 175Q74 377' +
    ' 74 698Q74 1037 271 1248Q467 1458 793 1458Q1100 1458 1289 1257Q1479 1057' +
    ' 1479 727Q1479 390 1282 182Q1275 175 1269.0 168.5Q1263 162 1256 156L1619' +
    ' -193H1167L977 0Q883 -25 770 -25ZM784 1180Q615 1180 514 1052Q414 925 414' +
    ' 715Q414 502 514 378Q614 254 776 254Q943 254 1041 374Q1139 496 1139 709' +
    'Q1139 931 1044 1056Q950 1180 784 1180Z"/></g></svg>';
  footer.insertBefore(seal, identityLine);

  const links = document.createElement("div");
  links.className = "footer-links";
  links.innerHTML =
    `Created by <a href="https://ruslandubas.com">Ruslan Dubas</a>` +
    ` · <a href="https://github.com/quantrank500">Support this project</a>` +
    ` · <a href="glossary.html">Definitions</a>` +
    ` · <a href="faq.html">FAQ</a>` +
    ` · <a href="https://github.com/quantrank500/quantrank500">Code</a>` +
    ` · <a href="${API}/docs">APIs</a>` +
    ` · <a href="privacy.html">Privacy</a>` +
    (TRAFFIC_URL ? ` · <a href="${TRAFFIC_URL}">Traffic</a>` : "");
  footer.appendChild(links);

  if (SUPPORTERS.length) {
    const strip = document.createElement("div");
    strip.className = "supporters";
    strip.innerHTML = `<span class="supporters-label">Supported by</span>` +
      SUPPORTERS.map(s =>
        `<a href="${s.url}">${escapeHtml(s.name)}</a>`).join("");
    footer.appendChild(strip);
  }
});

function renderFreshness(element, freshness) {
  const text = freshness
    ? `Data current through: ${freshness.data_current_through} close — settled ` +
      `${new Date(freshness.settled_at).toLocaleTimeString("en-US",
        { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" })} ET.`
    : "No settled sessions yet.";
  const strip = document.querySelector(".info-strip .strip-fresh");
  if (strip) strip.textContent = text;
  // footers now carry the identity sentence; only in-page lines still take the text
  if (element && element.tagName !== "FOOTER") element.textContent = text;
}
