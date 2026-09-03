// Named-client driver for the native-dom CDP frame-tree court.
//
// Reads one JSON command per line on stdin and answers one JSON line per
// command on stdout. It uses puppeteer-core only through puppeteer.connect,
// browser.targets/waitForTarget, target.createCDPSession, session.send,
// session.detach and browser.disconnect; it never calls target.page().
// The court records the exact client version from the installed package.

import { createRequire } from "node:module";
import { createInterface } from "node:readline";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const modulesRoot = process.argv[2];
const require = createRequire(join(modulesRoot, "package.json"));
const puppeteer = require("puppeteer-core");
const clientVersion = JSON.parse(readFileSync(join(modulesRoot, "node_modules", "puppeteer-core", "package.json"), "utf8")).version;

let browser = null;
const sessions = new Map();

function reply(value) {
  process.stdout.write(JSON.stringify(value) + "\n");
}

async function withTimeout(promise, ms) {
  let timer;
  const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`timeout after ${ms} ms`)), ms); });
  try { return await Promise.race([promise, timeout]); } finally { clearTimeout(timer); }
}

async function send(sessionName, method, params) {
  const session = sessions.get(sessionName);
  if (!session) throw new Error(`no session ${sessionName}`);
  try {
    return { ok: true, result: await withTimeout(session.send(method, params || {}), 10000) };
  } catch (error) {
    const message = String(error.message || error);
    const code = /^Protocol error \([^)]*\): (-?\d+)?/.exec(message);
    return { ok: false, error: { message: message.slice(0, 300), protocol_code: code && code[1] ? Number(code[1]) : null } };
  }
}

const handlers = {
  async version() {
    return { client: "puppeteer-core", version: clientVersion, node: process.version };
  },
  async connect({ endpoint }) {
    browser = await withTimeout(puppeteer.connect({ browserWSEndpoint: endpoint, protocol: "cdp" }), 10000);
    return { connected: true };
  },
  async targets() {
    const list = browser.targets().map((t) => ({ id: t._targetId, type: t.type(), url: t.url() }));
    return { targets: list };
  },
  async waitForTarget({ id }) {
    const target = await withTimeout(browser.waitForTarget((t) => t._targetId === id, { timeout: 5000 }), 6000);
    return { id: target._targetId, type: target.type(), url: target.url() };
  },
  async attach({ name, id }) {
    const target = browser.targets().find((t) => t._targetId === id);
    if (!target) throw new Error(`target ${id} is not listed`);
    const session = await withTimeout(target.createCDPSession(), 10000);
    sessions.set(name, session);
    return { attached: true, session_id: session.id() };
  },
  async send({ name, method, params }) {
    return send(name, method, params);
  },
  async detach({ name }) {
    const session = sessions.get(name);
    if (!session) throw new Error(`no session ${name}`);
    try { await withTimeout(session.detach(), 5000); return { detached: true }; }
    catch (error) { return { detached: false, error: String(error.message || error).slice(0, 300) }; }
    finally { sessions.delete(name); }
  },
  async disconnect() {
    if (browser) { await browser.disconnect(); browser = null; }
    sessions.clear();
    return { disconnected: true };
  },
};

const lines = createInterface({ input: process.stdin });
for await (const line of lines) {
  if (!line.trim()) continue;
  let command;
  try { command = JSON.parse(line); } catch { reply({ ok: false, error: "malformed command" }); continue; }
  const handler = handlers[command.command];
  if (!handler) { reply({ ok: false, error: `unknown command ${command.command}` }); continue; }
  try { reply({ ok: true, ...(await handler(command)) }); }
  catch (error) { reply({ ok: false, error: String(error.message || error).slice(0, 300) }); }
  if (command.command === "disconnect") break;
}
process.exit(0);
