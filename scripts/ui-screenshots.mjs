// Captures every UI page with headless Chromium and reports console errors.
// Usage (from scripts/ui-screenshots.sh): node ui-screenshots.mjs <base-url> <out-dir>
//
// DEMO=1: presentation mode for slides. Every API response is rewritten in
// the browser before it is displayed — device names, users, guests, SSIDs,
// venues, AP names, MAC addresses, public IPv6 addresses and Ruckus One
// identifiers are replaced by consistent fictitious values (the same device
// keeps the same fake name on every page). Requests made with fake values are
// translated back, so the pages keep working. Nothing is changed on the server.
// After each capture, the page text is checked for any real value left
// ("LEAK" lines). It also adds a simulated sponsored guest (Ruckus One
// "HostGuest") on a second device: investigation-sponsored / event-sponsored.
import { chromium } from "playwright";

const [base = "https://127.0.0.1", out = "/out"] = process.argv.slice(2);
const DEMO = process.env.DEMO === "1";

// ── Demo anonymizer ───────────────────────────────────────────────────────────
const fwd = new Map();   // real → fake
const rev = new Map();   // fake → real
const counters = {};
const pad = (n) => String(n).padStart(2, "0");
const next = (k) => (counters[k] = (counters[k] || 0) + 1);

const SSIDS  = ["Corp-WiFi", "Guest-WiFi", "IoT-Net", "Staff-WiFi", "Lab-WiFi", "Voice-WiFi"];
const VENUES = ["HQ-Geneva", "Branch-Lyon", "Warehouse-Annecy", "Office-Paris", "Campus-Lausanne"];
const AP_AREAS = ["Lobby", "Open-Space", "Meeting-1", "Meeting-2", "Cafeteria", "Floor-2", "Outdoor", "Reception"];

function deviceName(real) {
  const kinds = [
    [/ipad/i, "iPad-Ops"], [/iphone/i, "iPhone-Sales"], [/macbook|mbp|imac/i, "MacBook-Eng"],
    [/android|galaxy|pixel|samsung/i, "Android-Field"], [/google-home|nest|echo|alexa/i, "Smart-Speaker"],
    [/yeelink|light|lamp|hue/i, "Smart-Light"], [/wyze|cam/i, "Camera"], [/printer|epson|brother|hp-/i, "Printer"],
    [/tv|roku|chromecast|apple-tv/i, "Display"], [/^wlan\d|esp|tasmota|shelly|sensor/i, "IoT-Sensor"],
    [/pc|desktop|laptop|win/i, "Laptop-Finance"],
  ];
  const base_ = (kinds.find(([re]) => re.test(real)) || [null, "Laptop-HR"])[1];
  return `${base_}-${pad(next(base_))}`;
}

const FAKERS = {
  device: deviceName,
  user:   () => `user${pad(next("user"))}`,
  person: () => `Guest Visitor ${next("person")}`,
  email:  () => `guest${pad(next("email"))}@example.com`,
  phone:  () => `+41 22 000 00 ${pad(next("phone"))}`,
  ssid:   () => SSIDS[(next("ssid") - 1) % SSIDS.length],
  venue:  () => VENUES[(next("venue") - 1) % VENUES.length],
  ap:     () => `AP-${AP_AREAS[(next("ap") - 1) % AP_AREAS.length]}`,
  id:     () => `00000000-0000-0000-0000-0000000000${pad(next("id"))}`,
  host:   () => `controller-${next("host")}.example.com`,
};
// Field name → kind of value. MAC-looking values are handled by MAC_RE.
const FIELD_KIND = {
  hostname: "device", client_label: "device", alias: "device",
  username: "user", guest_name: "person", email: "email", sponsor_email: "email", phone: "phone",
  ssid: "ssid", venue: "venue", zone_name: "venue", ap_name: "ap",
  tenant_id: "id", client_id: "id", host: "host",
};
const LIST_KIND = { aps: "ap", venues: "venue", ssids: "ssid" };   // /options lists
const MAC_RE  = /\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b/g;
const IPV6_RE = /\b2[0-9a-f]{3}:[0-9a-f]{1,4}:[0-9a-f:]{2,}[0-9a-f]\b/gi;   // global unicast
const GUID_RE = /^[0-9a-f]{32}$/i;

// Generic words, not personal data (the product name, default accounts...):
// left as they are, and not reported as leaks.
const GENERIC = new Set(["admin", "administrator", "bloodhound", "root", "guest", "user", "default"]);

function register(real, kind) {
  if (typeof real !== "string" || real.length < 3 || fwd.has(real) || rev.has(real)) return;
  if (GENERIC.has(real.toLowerCase())) return;
  if (GUID_RE.test(real) || real.match(MAC_RE)?.[0] === real || /^[\d.]+$/.test(real)) return;
  const fake = FAKERS[kind](real);
  fwd.set(real, fake);
  rev.set(fake, real);
}
function fakeMac(real) {
  const key = real.toLowerCase().replace(/-/g, ":");
  if (!fwd.has(key)) {
    const n = next("mac");
    const hex = (x) => (x & 255).toString(16).padStart(2, "0");
    const f = `02:00:5e:${hex(n >> 16)}:${hex(n >> 8)}:${hex(n)}`;
    fwd.set(key, f);
    rev.set(f, key);
  }
  const f = fwd.get(key);
  return real === real.toUpperCase() ? f.toUpperCase() : f;
}
function fakeIpv6(real) {
  const key = real.toLowerCase();
  if (!fwd.has(key)) {
    const f = `2001:db8:${next("ipv6").toString(16)}::1`;
    fwd.set(key, f);
    rev.set(f, key);
  }
  return fwd.get(key);
}
function collect(node, key) {
  if (Array.isArray(node)) node.forEach((v) => (LIST_KIND[key] && typeof v === "string") ? register(v, LIST_KIND[key]) : collect(v, key));
  else if (node && typeof node === "object") for (const [k, v] of Object.entries(node)) { if (!SKIP_KEYS.has(k)) collect(v, k); }
  else if (typeof node === "string" && FIELD_KIND[key]) register(node, FIELD_KIND[key]);
}
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// Technical values the UI relies on: never rewritten (e.g. role "admin" must
// stay "admin" even if a platform username is also "admin").
const SKIP_KEYS = new Set(["role", "roles", "active", "platform", "guest_type", "proto", "status",
  "type", "enabled", "region", "source", "phase", "detail", "query", "timestamp", "first_seen", "last_seen"]);
// Free text that may contain names inside a longer string (raw syslog line…)
const FREE_TEXT_KEYS = new Set(["message", "full_message", "dst_hostname"]);
function anonymizeString(value, key) {
  let s = value.replace(MAC_RE, fakeMac).replace(IPV6_RE, fakeIpv6);
  if (fwd.has(s)) return fwd.get(s);
  if (FREE_TEXT_KEYS.has(key)) {
    const entries = [...fwd.entries()].filter(([k]) => k.length >= 5 && !k.includes(":"))
      .sort((a, b) => b[0].length - a[0].length);
    for (const [real, fake] of entries) s = s.replace(new RegExp(escapeRe(real), "g"), fake);
  }
  return s;
}
function rewrite(node, key) {
  if (Array.isArray(node)) return node.map((v) => rewrite(v, key));
  if (node && typeof node === "object") {
    return Object.fromEntries(Object.entries(node).map(([k, v]) => [k, SKIP_KEYS.has(k) ? v : rewrite(v, k)]));
  }
  return typeof node === "string" ? anonymizeString(node, key) : node;
}
function anonymize(json) { collect(json); return rewrite(json); }
function deanonymize(s) {
  if (!s) return s;
  let out = s;
  const entries = [...rev.entries()].sort((a, b) => b[0].length - a[0].length);
  for (const [fake, real] of entries) {
    for (const form of new Set([fake, fake.toUpperCase(), encodeURIComponent(fake), encodeURIComponent(fake.toUpperCase())])) {
      if (out.includes(form)) out = out.split(form).join(form === fake.toUpperCase() || form === encodeURIComponent(fake.toUpperCase()) ? real.toUpperCase() : real);
    }
  }
  return out;
}

// ── Simulated sponsored guest (DEMO) ─────────────────────────────────────────
let sponsoredMac = null;   // real MAC of the device shown as a sponsored guest
const now = Date.now();
const SPONSORED = {
  is_guest: true, guest_type: "HostGuest", guest_name: "Alex Martin", phone: "+41 79 000 00 42",
  email: "alex.martin@example.org", sponsor_email: "host.sponsor@example.com", pass_duration_hours: 24,
  creation_date: new Date(now - 3 * 3600e3).toISOString(), expiry_date: new Date(now + 21 * 3600e3).toISOString(),
  ssid: "Guest-WiFi", client_label: "Alex Martin",
  hostname: "Galaxy-S24", username: "alex.martin", os_type: "Android", device_type: "Smartphone",
};
// Already fictitious: never rename them
for (const v of Object.values(SPONSORED)) if (typeof v === "string") rev.set(v, v);

function addSponsored(json, url) {
  if (!sponsoredMac) return json;
  const same = (m) => typeof m === "string" && m.toLowerCase() === sponsoredMac.toLowerCase();
  if (url.includes("/lookup/mac/") && same(decodeURIComponent(url.split("/lookup/mac/")[1].split(/[/?]/)[0])) && !url.includes("/timeline")) {
    return { ...json, ...SPONSORED };
  }
  if (Array.isArray(json?.logs)) {
    json.logs = json.logs.map((l) => (same(l.client_mac) ? { ...l, ...SPONSORED } : l));
  }
  return json;
}

// ── Browser ───────────────────────────────────────────────────────────────────
const browser = await chromium.launch();
const ctx = await browser.newContext({
  ignoreHTTPSErrors: true,
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: Number(process.env.SCALE ?? 1),   // 2 = sharper images for slides
  colorScheme: process.env.SCHEME ?? "light",
});

if (DEMO) {
  await ctx.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const realUrl = deanonymize(req.url());
    const realBody = req.postData() ? deanonymize(req.postData()) : undefined;
    const resp = await route.fetch({ url: realUrl, postData: realBody });
    const type = resp.headers()["content-type"] || "";
    if (!type.includes("application/json")) return route.fulfill({ response: resp });
    let json = await resp.json().catch(() => null);
    if (json === null) return route.fulfill({ response: resp });
    json = anonymize(addSponsored(json, realUrl));
    return route.fulfill({ response: resp, json });
  });
}

// Sign in with the dedicated ui-test account (see ui-screenshots.sh)
const r = await ctx.request.post(base + "/api/v1/auth/login", {
  data: { username: process.env.UI_USER, password: process.env.UI_PASSWORD },
});
if (!r.ok()) {
  console.error(`login failed: ${r.status()}`);
  process.exit(2);
}
const me = await (await ctx.request.get(base + "/api/v1/auth/me")).json();

// Real clients and flows, for the investigation pages (APIRequestContext is
// not routed: values are anonymized here before being put in URLs)
const found = await (await ctx.request.get(base + "/api/v1/search?limit=200")).json().catch(() => ({}));
const logs = (found.logs || []).filter((l) => l.client_mac);
// Prefer a device with a real name (a MAC as title looks poor on a slide)
const hasName = (l) => l.client_label && !/^(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}$/i.test(l.client_label);
const named = logs.find(hasName) || logs[0] || null;
const other = logs.find((l) => named && l.client_mac !== named.client_mac) || null;

function eventPath(log) {
  const l = DEMO ? anonymize(structuredClone(log)) : log;
  const ev = new URLSearchParams({
    ts: l.timestamp || "", src_ip: l.src_ip || "", dst_ip: l.dst_ip || "",
    dst_host: l.dst_hostname || l.dst_ip || "", dst_port: String(l.dst_port || ""),
    proto: l.proto || "", ap: l.ap_name || "", venue: l.venue || "", ssid: l.ssid || "",
    id: l.id || "", platform: l.platform || "",
  });
  return `/investigate/${encodeURIComponent(l.client_mac)}/event?${ev}`;
}
const macPath = (log) => `/investigate/${encodeURIComponent(DEMO ? fakeMac(log.client_mac) : log.client_mac)}`;

const pages = [["search", "/"]];
if (named) pages.push(["investigation", macPath(named)], ["event", eventPath(named)]);
else console.log("no flow log found: investigation pages skipped");
if (DEMO && other) {
  sponsoredMac = other.client_mac;
  const guestLog = { ...other, ...SPONSORED };
  pages.push(["investigation-sponsored", macPath(other)], ["event-sponsored", eventPath(guestLog)]);
}
// Settings tabs are buttons on /settings (not URLs): [file name, tab label]
const SETTINGS = me.role === "admin"
  ? [["ruckus-one", "Ruckus One"], ["unleashed", "Unleashed"], ["smartzone", "SmartZone"],
     ["preferences", "Preferences"], ["disk", "Disk & Logs"], ["password", "Password"],
     ["users", "Users"], ["ssl", "SSL"]]
  : me.role === "manager" ? [["disk", "Disk & Logs"], ["password", "Password"]] : [["password", "Password"]];
for (const [name, label] of SETTINGS) pages.push([`settings-${name}`, "/settings", label]);

let failures = 0;
for (const [name, path, tab] of pages) {
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (resp) => resp.status() >= 400 && errors.push(`${resp.status()} ${resp.url()}`));
  await page.goto(base + path, { waitUntil: "networkidle" });
  if (tab) {
    await page.locator(".tab-item", { hasText: tab }).first().click();
    await page.waitForLoadState("networkidle");
  }
  if (name === "search") {
    // The search page is empty until a search is run
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await page.waitForLoadState("networkidle");
  }
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: false });
  let leaks = [];
  if (DEMO) {
    const text = await page.locator("body").innerText();
    const values = await page.locator("input").evaluateAll((els) => els.map((e) => e.value).join(" "));
    const shown = text + " " + values;
    const low = shown.toLowerCase();
    const fakes = [...fwd.values()].map((f) => f.toLowerCase());
    // a real value that is part of a fake one ("iPhone" in "iPhone-Sales-01") is not a leak
    leaks = [...fwd.keys()].filter((real) => real.length >= 4 && low.includes(real.toLowerCase())
      && !fakes.some((f) => f.includes(real.toLowerCase())));
    if (leaks.length) console.log(`${name}: LEAK ${leaks.slice(0, 10).join(", ")}`);
  }
  console.log(`${name}: ${errors.length ? "ERRORS " + errors.join(" | ") : "ok"}`);
  failures += errors.length + leaks.length;
  await page.close();
}
await ctx.request.post(base + "/api/v1/auth/logout").catch(() => {});
await browser.close();
process.exit(failures ? 1 : 0);
