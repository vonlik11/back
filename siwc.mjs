import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import http from "node:http";
import { fileURLToPath } from "node:url";
import { createRemoteJWKSet, jwtVerify } from "jose";

const directory = path.dirname(fileURLToPath(import.meta.url));
const authDir = path.join(directory, ".auth");
const credFile = path.join(authDir, "chatgpt.enc");
const hostFile = path.join(authDir, "host.json");
const registrationFile = path.join(authDir, "registration.json");
const callback = "http://127.0.0.1:1455/auth/callback";
const resource = "https://api.openai.com/v1";
const issuer = "https://auth.openai.com";
const tokenEndpoint = `${issuer}/api/accounts/oauth/token`;
const jwks = createRemoteJWKSet(new URL(`${issuer}/.well-known/jwks.json`));

function stableHostId() {
  fs.mkdirSync(authDir, { recursive: true });
  if (fs.existsSync(hostFile)) return JSON.parse(fs.readFileSync(hostFile, "utf8")).id;
  const id = `urn:uuid:${crypto.randomUUID()}`;
  fs.writeFileSync(hostFile, JSON.stringify({ id }, null, 2), { mode: 0o600 });
  return id;
}

function windowsDpapi(operation, data) {
  const script = [
    "$ErrorActionPreference = 'Stop'",
    "Add-Type -AssemblyName System.Security",
    "$inputBytes = [Convert]::FromBase64String([Console]::In.ReadToEnd())",
    `$outputBytes = [System.Security.Cryptography.ProtectedData]::${operation}($inputBytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)`,
    "[Console]::Write([Convert]::ToBase64String($outputBytes))",
  ].join("; ");
  const result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    input: data,
    encoding: "utf8",
    windowsHide: true,
    timeout: 15_000,
  });
  const output = result.stdout?.trim();
  if (result.error || result.status !== 0 || !output || !/^[A-Za-z0-9+/]+={0,2}$/.test(output)) {
    const action = operation === "Protect" ? "сохранить" : "прочитать";
    const detail = result.error?.code || (result.status !== 0 ? `код выхода ${result.status}` : "пустой или неверный результат");
    throw new Error(`Windows DPAPI не смог ${action} сессию ChatGPT (${detail}).`);
  }
  return output;
}

function protect(value) {
  const b64 = Buffer.from(value, "utf8").toString("base64");
  if (process.platform === "win32") {
    return JSON.stringify({ version: 1, platform: "win32-dpapi", data: windowsDpapi("Protect", b64) });
  }
  if (process.platform === "linux" || process.platform === "darwin") return JSON.stringify({ version: 1, platform: "file-permissions", data: b64 });
  throw new Error("Для этой операционной системы нет защищённого хранилища токена.");
}

function unprotect(envelope) {
  const parsed = JSON.parse(envelope);
  if (parsed.platform === "win32-dpapi" && process.platform === "win32") {
    return Buffer.from(windowsDpapi("Unprotect", parsed.data), "base64").toString("utf8");
  }
  if (parsed.platform === "file-permissions" && process.platform !== "win32") return Buffer.from(parsed.data, "base64").toString("utf8");
  throw new Error("Хранилище ChatGPT создано на другой платформе. Не переносите этот файл; выполните вход заново.");
}

function saveCredentials(credentials) {
  fs.mkdirSync(authDir, { recursive: true });
  const tmp = `${credFile}.tmp`;
  fs.writeFileSync(tmp, protect(JSON.stringify(credentials)), { mode: 0o600 });
  fs.renameSync(tmp, credFile);
}

function loadCredentials() {
  if (!fs.existsSync(credFile)) throw new Error("ChatGPT ещё не подключён. Выполните `npm run login` в папке bot.");
  return JSON.parse(unprotect(fs.readFileSync(credFile, "utf8")));
}

function b64url(buffer) { return Buffer.from(buffer).toString("base64url"); }
async function verifyIdToken(idToken, clientId, nonce) {
  const { payload } = await jwtVerify(idToken, jwks, { issuer, audience: clientId, algorithms: ["RS256"] });
  if (nonce && payload.nonce !== nonce) throw new Error("ID token не прошёл проверку nonce.");
  if (typeof payload.sub !== "string") throw new Error("В проверенном ID token нет subject.");
  return payload;
}

async function postForm(data) {
  const response = await fetch(tokenEndpoint, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: new URLSearchParams(data) });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (body.error === "invalid_grant") throw new Error("Token endpoint: invalid_grant. Код истёк, уже использован или не совпали параметры OAuth. Сохранённый client_id оставлен для нового входа; запустите `npm run login` ещё раз и используйте только новую ссылку.");
    throw new Error(`Token endpoint: ${body.error_description || body.error || response.status}`);
  }
  return body;
}

async function login(forceNewRegistration = false) {
  if (forceNewRegistration && fs.existsSync(credFile)) throw new Error("Уже есть подключённый ChatGPT-аккаунт. Сначала используйте обычный `npm run login`, чтобы не перезаписать его.");
  if (fs.existsSync(credFile)) console.log("Существующее подключение будет заменено после успешной повторной авторизации.");
  const state = crypto.randomBytes(32).toString("base64url");
  const nonce = crypto.randomBytes(32).toString("base64url");
  const verifier = crypto.randomBytes(48).toString("base64url");
  const challenge = b64url(crypto.createHash("sha256").update(verifier).digest());
  const hostId = stableHostId();
  const existing = !forceNewRegistration && fs.existsSync(credFile) ? loadCredentials() : null;
  const pendingRegistration = !forceNewRegistration && fs.existsSync(registrationFile) ? JSON.parse(fs.readFileSync(registrationFile, "utf8")) : null;
  const clientId = existing?.clientId || pendingRegistration?.clientId || "dynamic_agent_client";
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, callback);
    if (req.method !== "GET" || url.pathname !== "/auth/callback") { res.writeHead(404).end("Not found"); return; }
    if (url.searchParams.get("state") !== state) { res.writeHead(400).end("State validation failed"); server.close(); return; }
    if (url.searchParams.get("error")) { res.writeHead(400, { "content-type": "text/html; charset=utf-8" }).end("<h2>Вход не завершён. Закройте вкладку и повторите запуск.</h2>"); console.error(`Sign-in declined: ${url.searchParams.get("error")}`); server.close(); return; }
    const code = url.searchParams.get("code");
    const returnedClientId = url.searchParams.get("client_id") || pendingRegistration?.clientId || existing?.clientId;
    if (!code || !returnedClientId || returnedClientId === "dynamic_agent_client") { res.writeHead(400).end("OpenAI не вернул issued client_id."); server.close(); return; }
    try {
      if (!forceNewRegistration && pendingRegistration?.clientId && returnedClientId !== pendingRegistration.clientId) throw new Error("OpenAI вернул другой client_id, чем сохранённая регистрация.");
      fs.mkdirSync(authDir, { recursive: true });
      fs.writeFileSync(registrationFile, JSON.stringify({ clientId: returnedClientId, hostId }, null, 2), { mode: 0o600 });
      const tokens = await postForm({ grant_type: "authorization_code", client_id: returnedClientId, code, code_verifier: verifier, redirect_uri: callback, resource });
      const scopes = String(tokens.scope || url.searchParams.get("scope") || "").split(/\s+/).filter(Boolean);
      if (!scopes.includes("chatgpt.tokens.use.direct")) throw new Error("ChatGPT-план не разрешён. В браузере нужно согласиться на использование плана.");
      const identity = await verifyIdToken(tokens.id_token, returnedClientId, nonce);
      const credentials = { clientId: returnedClientId, hostId, subject: identity.sub, email: identity.email || "", idToken: tokens.id_token, accessToken: tokens.access_token, refreshToken: tokens.refresh_token, expiresAt: Date.now() + Number(tokens.expires_in || 3600) * 1000, scopes };
      saveCredentials(credentials);
      fs.rmSync(registrationFile, { force: true });
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" }).end("<meta charset='utf-8'><h2>ChatGPT подключён ✅</h2><p>Можно закрыть эту вкладку и вернуться в Telegram.</p>");
      console.log(`ChatGPT подключён для ${credentials.email || "аккаунта"}; разрешение на план получено.`);
    } catch (error) {
      res.writeHead(400, { "content-type": "text/html; charset=utf-8" }).end("<meta charset='utf-8'><h2>Не удалось завершить подключение</h2><p>См. сообщение в терминале.</p>");
      console.error(error.message);
      process.exitCode = 1;
    } finally { server.close(); }
  });
  await new Promise((resolve, reject) => server.once("error", reject).listen(1455, "127.0.0.1", resolve));
  const params = new URLSearchParams({ client_id: clientId, ext_agent_host_id: hostId, response_type: "code", redirect_uri: callback, scope: "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct", resource, state, nonce, code_challenge_method: "S256", code_challenge: challenge });
  if (clientId === "dynamic_agent_client") params.set("agent_name_hint", "AI-маркетолог");
  else if (existing?.idToken) params.set("id_token_hint", existing.idToken);
  else if (existing?.email) params.set("login_hint", existing.email);
  const authUrl = `${issuer}/api/accounts/authorize?${params}`;
  console.log(`Откройте страницу входа OpenAI в браузере${clientId === "dynamic_agent_client" ? " (первая регистрация)" : " (повторный вход в сохранённую регистрацию)"}. Не копируйте адрес страницы: он содержит временные параметры OAuth.`);
  if (process.platform === "win32") spawnSync("rundll32.exe", ["url.dll,FileProtocolHandler", authUrl], { windowsHide: true, stdio: "ignore" });
  else if (process.platform === "darwin") spawnSync("open", [authUrl], { stdio: "ignore" });
  else spawnSync("xdg-open", [authUrl], { stdio: "ignore" });
  await new Promise((resolve) => server.once("close", resolve));
}

async function getAccessToken() {
  const c = loadCredentials();
  if (c.expiresAt > Date.now() + 60_000) return c;
  const next = await postForm({ grant_type: "refresh_token", client_id: c.clientId, refresh_token: c.refreshToken, resource });
  const scopes = String(next.scope || c.scopes.join(" ")).split(/\s+/).filter(Boolean);
  if (!scopes.includes("chatgpt.tokens.use.direct")) throw new Error("Обновлённая ChatGPT-сессия не разрешает использование плана.");
  const idToken = next.id_token || c.idToken;
  const identity = await verifyIdToken(idToken, c.clientId);
  if (identity.sub !== c.subject) throw new Error("Аккаунт ChatGPT изменился; остановлен запрос токена.");
  const updated = { ...c, idToken, accessToken: next.access_token, refreshToken: next.refresh_token || c.refreshToken, expiresAt: Date.now() + Number(next.expires_in || 3600) * 1000, scopes };
  saveCredentials(updated);
  return updated;
}

async function infer(payload) {
  const c = await getAccessToken();
  const input = [];
  for (const item of payload.history || []) input.push({ role: item.role === "assistant" ? "assistant" : "user", content: item.text });
  input.push({ role: "user", content: payload.prompt });
  const response = await fetch("https://api.openai.com/v1/responses", { method: "POST", headers: { authorization: `Bearer ${c.accessToken}`, "content-type": "application/json" }, body: JSON.stringify({ model: process.env.CHATGPT_MODEL || "gpt-6.1-sol", instructions: payload.instructions, input, store: false, stream: true }) });
  if (!response.ok) {
    const details = await response.text();
    throw new Error(`ChatGPT plan request: ${response.status} ${details.slice(0, 350)}`);
  }
  let buffer = "", text = "", completed = false;
  for await (const chunk of response.body.pipeThrough(new TextDecoderStream())) {
    buffer += chunk;
    const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n"); buffer = blocks.pop() || "";
    for (const block of blocks) {
      const data = block.split("\n").find(line => line.startsWith("data: "))?.slice(6);
      if (!data || data === "[DONE]") continue;
      let event; try { event = JSON.parse(data); } catch { continue; }
      if (event.type === "response.output_text.delta") text += event.delta || "";
      else if (event.type === "response.failed") throw new Error(`ChatGPT response failed: ${event.response?.error?.code || "unknown_error"}`);
      else if (event.type === "response.completed") completed = true;
    }
  }
  if (!completed || !text.trim()) throw new Error("ChatGPT stream ended before response.completed.");
  process.stdout.write(JSON.stringify({ text }));
}

const command = process.argv[2];
if (command === "login") await login();
else if (command === "login-new") await login(true);
else if (command === "status") {
  try { const c = loadCredentials(); console.log(JSON.stringify({ connected: true, email: c.email, expiresAt: c.expiresAt, sharing: c.scopes.includes("chatgpt.tokens.use.direct") })); }
  catch { console.log(JSON.stringify({ connected: false })); }
} else if (command === "infer") {
  let input = ""; for await (const chunk of process.stdin) input += chunk;
  try { await infer(JSON.parse(input)); }
  catch (error) { process.stderr.write(error.message); process.exitCode = 1; }
} else { console.error("Use: node siwc.mjs login | status | infer"); process.exitCode = 2; }
