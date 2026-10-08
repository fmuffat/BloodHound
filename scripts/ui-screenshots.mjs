// Captures every UI page with headless Chromium and reports console errors.
// Usage (from scripts/ui-screenshots.sh): node ui-screenshots.mjs <base-url> <out-dir>
import { chromium } from "playwright";

const [base = "https://127.0.0.1", out = "/out"] = process.argv.slice(2);

const browser = await chromium.launch();
const ctx = await browser.newContext({
  ignoreHTTPSErrors: true,
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: Number(process.env.SCALE ?? 1),   // 2 = sharper images for slides
  colorScheme: process.env.SCHEME ?? "light",
});

// Sign in with the dedicated ui-test account (see ui-screenshots.sh)
const r = await ctx.request.post(base + "/api/v1/auth/login", {
  data: { username: process.env.UI_USER, password: process.env.UI_PASSWORD },
});
if (!r.ok()) {
  console.error(`login failed: ${r.status()}`);
  process.exit(2);
}
const me = await (await ctx.request.get(base + "/api/v1/auth/me")).json();

// A real client and one of its flows, for the investigation pages
const found = await (await ctx.request.get(base + "/api/v1/search?limit=50")).json().catch(() => ({}));
const log = (found.logs || []).find((l) => l.client_mac) || null;
const pages = [["search", "/"]];
if (log) {
  const mac = encodeURIComponent(log.client_mac);
  const ev = new URLSearchParams({
    ts: log.timestamp || "", src_ip: log.src_ip || "", dst_ip: log.dst_ip || "",
    dst_host: log.dst_hostname || log.dst_ip || "", dst_port: String(log.dst_port || ""),
    proto: log.proto || "", ap: log.ap_name || "", venue: log.venue || "", ssid: log.ssid || "",
    id: log.id || "", platform: log.platform || "",
  });
  pages.push(["investigation", `/investigate/${mac}`], ["event", `/investigate/${mac}/event?${ev}`]);
} else {
  console.log("no flow log found: investigation pages skipped");
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
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: false });
  console.log(`${name}: ${errors.length ? "ERRORS " + errors.join(" | ") : "ok"}`);
  failures += errors.length;
  await page.close();
}
await ctx.request.post(base + "/api/v1/auth/logout").catch(() => {});
await browser.close();
process.exit(failures ? 1 : 0);
