import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import http from "node:http";
import { fileURLToPath } from "node:url";
import { createRemoteJWKSet, jwtVerify } from "jose";

const directory = path.dirname(fileURLToPath(import.meta.url));
const command = process.argv[2];
const telegramId = process.argv[3];
const platform = command?.endsWith("-max") ? "max" : command?.endsWith("-vk") ? "vk" : "telegram";
const accountCommand = ["login-telegram", "login-max", "login-vk", "api-login", "api-login-max", "api-login-vk", "infer-max", "infer-api-max", "connect-api-max", "infer-vk", "infer-api-vk", "connect-api-vk"].includes(command);
if ((telegramId !== undefined && !/^[1-9][0-9]{0,15}$/.test(telegramId)) || (["login-telegram", "login-max", "login-vk"].includes(command) && !telegramId)) {
  console.error("Укажите корректный ID аккаунта.");
  process.exit(2);
}
const authRoot = path.join(directory, ".auth");
const authDir = telegramId ? path.join(authRoot, platform, telegramId) : authRoot;
const credFile = path.join(authDir, "chatgpt.enc");
const apiKeyFile = path.join(authDir, "api-key.enc");
const providerFile = path.join(authDir, "provider");
const hostFile = path.join(authDir, "host.json");
const registrationFile = path.join(authDir, "registration.json");
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
  writePrivateFile(credFile, protect(JSON.stringify(credentials)));
}

function writePrivateFile(file, contents) {
  fs.mkdirSync(authDir, { recursive: true });
  const tmp = `${file}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(tmp, contents, { mode: 0o600 });
  try {
    fs.renameSync(tmp, file);
  } finally {
    fs.rmSync(tmp, { force: true });
  }
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
  const response = await fetch(tokenEndpoint, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: new URLSearchParams(data), signal: AbortSignal.timeout(30_000) });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (body.error === "invalid_grant") throw new Error("Token endpoint: invalid_grant. Код истёк, уже использован или не совпали параметры OAuth. Сохранённый client_id оставлен для нового входа; запустите `npm run login` ещё раз и используйте только новую ссылку.");
    throw new Error(`Token endpoint: ${body.error_description || body.error || response.status}`);
  }
  return body;
}

async function login(forceNewRegistration = false, telegram = false) {
  if (forceNewRegistration && fs.existsSync(credFile)) throw new Error("Уже есть подключённый ChatGPT-аккаунт. Сначала используйте обычный `npm run login`, чтобы не перезаписать его.");
  if (!telegram && fs.existsSync(credFile)) console.log("Существующее подключение будет заменено после успешной повторной авторизации.");
  const state = crypto.randomBytes(32).toString("base64url");
  const nonce = crypto.randomBytes(32).toString("base64url");
  const verifier = crypto.randomBytes(48).toString("base64url");
  const challenge = b64url(crypto.createHash("sha256").update(verifier).digest());
  const hostId = stableHostId();
  const existing = !forceNewRegistration && fs.existsSync(credFile) ? loadCredentials() : null;
  const pendingRegistration = !forceNewRegistration && fs.existsSync(registrationFile) ? JSON.parse(fs.readFileSync(registrationFile, "utf8")) : null;
  const clientId = existing?.clientId || pendingRegistration?.clientId || "dynamic_agent_client";
  let callback;
  let consumed = false;
  const server = http.createServer(async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Referrer-Policy", "no-referrer");
    const url = new URL(req.url, callback);
    if (req.method !== "GET" || url.pathname !== "/auth/callback") { res.writeHead(404).end("Not found"); return; }
    if (consumed || url.searchParams.get("state") !== state) { res.writeHead(400).end("State validation failed"); return; }
    consumed = true;
    if (url.searchParams.get("error")) { res.writeHead(400, { "content-type": "text/html; charset=utf-8" }).end("<h2>Вход не завершён. Закройте вкладку и повторите подключение.</h2>"); process.exitCode = 1; server.close(); return; }
    const code = url.searchParams.get("code");
    const returnedClientId = url.searchParams.get("client_id") || pendingRegistration?.clientId || existing?.clientId;
    if (!code || !returnedClientId || returnedClientId === "dynamic_agent_client") { res.writeHead(400).end("OpenAI не вернул данные для подключения."); process.exitCode = 1; server.close(); return; }
    try {
      if (clientId !== "dynamic_agent_client" && returnedClientId !== clientId) throw new Error("OpenAI вернул другой client_id, чем сохранённая регистрация.");
      fs.mkdirSync(authDir, { recursive: true });
      fs.writeFileSync(registrationFile, JSON.stringify({ clientId: returnedClientId, hostId }, null, 2), { mode: 0o600 });
      const tokens = await postForm({ grant_type: "authorization_code", client_id: returnedClientId, code, code_verifier: verifier, redirect_uri: callback, resource });
      const scopes = String(tokens.scope || "").split(/\s+/).filter(Boolean);
      if (!scopes.includes("chatgpt.tokens.use.direct")) throw new Error("ChatGPT-план не разрешён. В браузере нужно согласиться на использование плана.");
      const identity = await verifyIdToken(tokens.id_token, returnedClientId, nonce);
      const credentials = { clientId: returnedClientId, hostId, subject: identity.sub, email: identity.email || "", idToken: tokens.id_token, accessToken: tokens.access_token, refreshToken: tokens.refresh_token, expiresAt: Date.now() + Number(tokens.expires_in || 3600) * 1000, scopes };
      saveCredentials(credentials);
      if (telegram) writePrivateFile(providerFile, "chatgpt");
      fs.rmSync(registrationFile, { force: true });
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" }).end("<meta charset='utf-8'><h2>ChatGPT подключён ✅</h2><p>Можно закрыть эту вкладку и вернуться в Telegram.</p>");
      if (!telegram) console.log("ChatGPT подключён. Разрешение на использование плана получено.");
    } catch (error) {
      res.writeHead(400, { "content-type": "text/html; charset=utf-8" }).end("<meta charset='utf-8'><h2>Не удалось завершить подключение</h2><p>Вернитесь в приложение и повторите подключение.</p>");
      if (!telegram) console.error(error.message);
      process.exitCode = 1;
    } finally { server.close(); }
  });
  await new Promise((resolve, reject) => server.once("error", reject).listen(telegram ? 0 : 1455, "127.0.0.1", resolve));
  callback = `http://127.0.0.1:${server.address().port}/auth/callback`;
  const closed = new Promise((resolve) => server.once("close", resolve));
  const expiry = setTimeout(() => {
    if (consumed) return;
    process.exitCode = 1;
    server.close();
  }, 10 * 60 * 1000);
  const params = new URLSearchParams({ client_id: clientId, ext_agent_host_id: hostId, response_type: "code", redirect_uri: callback, scope: "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct", resource, state, nonce, code_challenge_method: "S256", code_challenge: challenge });
  if (clientId === "dynamic_agent_client") params.set("agent_name_hint", "AI-маркетолог");
  else if (!telegram && existing?.idToken) params.set("id_token_hint", existing.idToken);
  else if (!telegram && existing?.email) params.set("login_hint", existing.email);
  const authUrl = `${issuer}/api/accounts/authorize?${params}`;
  if (telegram) console.log(JSON.stringify({ url: authUrl }));
  else {
    console.log("Откройте страницу входа OpenAI в браузере. Не копируйте её адрес: он содержит временные параметры OAuth.");
    if (process.platform === "win32") spawnSync("rundll32.exe", ["url.dll,FileProtocolHandler", authUrl], { windowsHide: true, stdio: "ignore" });
    else if (process.platform === "darwin") spawnSync("open", [authUrl], { stdio: "ignore" });
    else spawnSync("xdg-open", [authUrl], { stdio: "ignore" });
  }
  await closed;
  clearTimeout(expiry);
}

async function getAccessToken() {
  const c = loadCredentials();
  if (!c.scopes.includes("chatgpt.tokens.use.direct")) throw new Error("ChatGPT-план не разрешён. Подключите ChatGPT заново.");
  if (c.expiresAt > Date.now() + 60_000) return c;
  const next = await postForm({ grant_type: "refresh_token", client_id: c.clientId, refresh_token: c.refreshToken, resource });
  const scopes = String(next.scope || c.scopes.join(" ")).split(/\s+/).filter(Boolean);
  if (!scopes.includes("chatgpt.tokens.use.direct")) throw new Error("Обновлённая ChatGPT-сессия не разрешает использование плана.");
  const idToken = next.id_token || c.idToken;
  if (next.id_token) {
    const identity = await verifyIdToken(idToken, c.clientId);
    if (identity.sub !== c.subject) throw new Error("Аккаунт ChatGPT изменился; остановлен запрос токена.");
  }
  const updated = { ...c, idToken, accessToken: next.access_token, refreshToken: next.refresh_token || c.refreshToken, expiresAt: Date.now() + Number(next.expires_in || 3600) * 1000, scopes };
  saveCredentials(updated);
  return updated;
}

const model = process.env.CHATGPT_MODEL || "gpt-6.1-sol";
const apiProviders = {
  openai: {
    name: "OpenAI API",
    model: process.env.OPENAI_API_MODEL || model,
    keyUrl: "https://platform.openai.com/api-keys",
  },
  gemini: {
    name: "Google Gemini API",
    model: process.env.GEMINI_MODEL || "gemini-3.8-flash",
    keyUrl: "https://aistudio.google.com/app/apikey",
  },
  claude: {
    name: "Anthropic Claude API",
    model: process.env.CLAUDE_MODEL || "claude-sonnet-5",
    keyUrl: "https://console.anthropic.com/settings/keys",
  },
  deepseek: {
    name: "DeepSeek API",
    model: process.env.DEEPSEEK_MODEL || "deepseek-flash",
    keyUrl: "https://platform.deepseek.com/api_keys",
  },
};

function apiError(response, body = "") {
  if (response.status === 401) throw new Error("api_http_401");
  if (response.status === 402) throw new Error("api_http_402");
  if (response.status === 403) throw new Error("api_http_403");
  if (response.status === 404) throw new Error("api_http_404");
  if (response.status === 429) throw new Error("api_http_429");
  if (!response.ok) throw new Error(`api_http_${response.status}`);
  if (body) throw new Error("api_unavailable");
}

function apiKeyHeaders(provider, apiKey) {
  if (provider === "gemini") return { "x-goog-api-key": apiKey, "content-type": "application/json" };
  if (provider === "claude") return { "x-api-key": apiKey, "anthropic-version": "2023-06-01", "content-type": "application/json" };
  return { authorization: `Bearer ${apiKey}`, "content-type": "application/json" };
}

function readApiCredential() {
  const value = unprotect(fs.readFileSync(apiKeyFile, "utf8"));
  try {
    const parsed = JSON.parse(value);
    if (parsed && typeof parsed === "object" && typeof parsed.apiKey === "string") return parsed;
  } catch { /* Backward compatibility with older installations storing only the key. */ }
  return { provider: "openai", apiKey: value, model: process.env.OPENAI_API_MODEL || model };
}

async function validateApiKey(provider, apiKey, providerModel) {
  const headers = apiKeyHeaders(provider, apiKey);
  let url;
  if (provider === "openai") url = `https://api.openai.com/v1/models/${encodeURIComponent(providerModel)}`;
  else if (provider === "gemini") url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(providerModel)}`;
  else if (provider === "claude") url = `https://api.anthropic.com/v1/models/${encodeURIComponent(providerModel)}`;
  else url = "https://api.deepseek.com/models";
  const response = await fetch(url, { headers, signal: AbortSignal.timeout(20_000), redirect: "error" });
  const raw = await response.text();
  if (!response.ok) apiError(response, raw);
  let data = {};
  try { data = JSON.parse(raw); } catch { throw new Error("api_unavailable"); }
  if (provider === "gemini" && Array.isArray(data.supportedGenerationMethods) && !data.supportedGenerationMethods.includes("generateContent")) {
    throw new Error("api_http_404");
  }
  if (provider === "deepseek" && Array.isArray(data.data) && !data.data.some(item => item.id === providerModel)) {
    throw new Error("api_http_404");
  }
}

async function connectApiKey(apiKey, provider = "openai") {
  const providerConfig = apiProviders[provider];
  if (!telegramId || !providerConfig || typeof apiKey !== "string" || apiKey.length < 10 || apiKey.length > 1000 || /\s/.test(apiKey) || (provider === "openai" && apiKey.startsWith("sk-admin-"))) {
    throw new Error("invalid_key");
  }
  await validateApiKey(provider, apiKey, providerConfig.model);
  writePrivateFile(apiKeyFile, protect(JSON.stringify({ provider, apiKey, model: providerConfig.model })));
  writePrivateFile(providerFile, "api");
}

function apiLoginPage(provider, token, message = "") {
  const name = apiProviders[provider].name;
  const status = message ? `<p role="status">${message}</p>` : "";
  return `<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Подключение AI API</title><style>body{font:16px system-ui;max-width:520px;margin:10vh auto;padding:24px;background:#f4f5f7;color:#202124}main{background:white;padding:28px;border-radius:16px;box-shadow:0 4px 22px #0001}label,input,button{display:block;width:100%;box-sizing:border-box;margin:12px 0}input{padding:12px;border:1px solid #bbb;border-radius:8px}button{padding:12px;background:#2463eb;color:white;border:0;border-radius:8px;font-weight:600}p{line-height:1.5}</style><main><h1>Подключить ${name}</h1>${status}<p>Ключ отправляется только локальному боту на этом компьютере. Он будет проверен и сохранён в зашифрованном локальном файле.</p><p><a href="${apiProviders[provider].keyUrl}" target="_blank" rel="noreferrer">Открыть страницу создания API-ключа</a></p><form method="post" action="/connect?token=${encodeURIComponent(token)}"><label for="apiKey">API-ключ</label><input id="apiKey" name="apiKey" type="password" required minlength="10" maxlength="1000" autocomplete="off" autocapitalize="off" spellcheck="false"><button type="submit">Проверить и подключить</button></form></main></html>`;
}

async function apiLogin(provider) {
  if (!telegramId || !apiProviders[provider]) throw new Error("Неизвестный API-провайдер или аккаунт.");
  const token = crypto.randomBytes(32).toString("base64url");
  let consumed = false, busy = false;
  const server = http.createServer(async (req, res) => {
    res.setHeader("Cache-Control", "no-store, max-age=0");
    res.setHeader("Pragma", "no-cache");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");
    const url = new URL(req.url, "http://127.0.0.1");
    const supplied = url.searchParams.get("token") || "";
    const validToken = supplied.length === token.length && crypto.timingSafeEqual(Buffer.from(supplied), Buffer.from(token));
    if (!validToken || consumed) { res.writeHead(404).end("Not found"); return; }
    if (req.method === "GET" && url.pathname === "/") {
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" }).end(apiLoginPage(provider, token));
      return;
    }
    if (req.method !== "POST" || url.pathname !== "/connect" || busy) { res.writeHead(404).end("Not found"); return; }
    busy = true;
    try {
      let raw = "";
      for await (const chunk of req) {
        raw += chunk;
        if (raw.length > 4096) throw new Error("invalid_key");
      }
      const apiKey = new URLSearchParams(raw).get("apiKey") || "";
      await connectApiKey(apiKey, provider);
      consumed = true;
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" }).end("<!doctype html><meta charset='utf-8'><title>Готово</title><h2>API подключён ✅</h2><p>Вернитесь в чат с ботом.</p>");
      server.close();
    } catch (error) {
      const messages = {
        invalid_key: "Проверьте, что это API-ключ выбранного сервиса, а не пароль или административный ключ.",
        api_http_401: "Провайдер отклонил ключ. Проверьте ключ или создайте новый.",
        api_http_402: "Проверьте оплату и баланс API-аккаунта.",
        api_http_403: "У ключа нет доступа к API или модели. Проверьте права проекта и доступность сервиса.",
        api_http_404: "Модель не найдена или недоступна ключу. Проверьте имя в .env и повторите подключение.",
        api_http_429: "Превышен лимит API-провайдера. Попробуйте позже или проверьте оплату.",
      };
      const notice = messages[error.message] || "Не удалось проверить ключ из-за ошибки соединения. Попробуйте ещё раз.";
      res.writeHead(400, { "content-type": "text/html; charset=utf-8" }).end(apiLoginPage(provider, token, notice));
      busy = false;
    }
  });
  await new Promise((resolve, reject) => server.once("error", reject).listen(0, "127.0.0.1", resolve));
  const closed = new Promise(resolve => server.once("close", resolve));
  const expiry = setTimeout(() => server.close(), 10 * 60 * 1000);
  const address = server.address();
  process.stdout.write(JSON.stringify({ url: `http://127.0.0.1:${address.port}/?token=${encodeURIComponent(token)}`, provider: apiProviders[provider].name }) + "\n");
  await closed;
  clearTimeout(expiry);
  if (!consumed) process.exitCode = 1;
}

async function responseText(response, label) {
  if (!response.ok) {
    const details = await response.text();
    if (label !== "ChatGPT plan") apiError(response, details);
    throw new Error(`${label} request: ${response.status} ${details.slice(0, 350)}`);
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
      else if (event.type === "response.failed") throw new Error(`${label} response failed: ${event.response?.error?.code || "unknown_error"}`);
      else if (event.type === "response.completed") completed = true;
    }
  }
  if (!completed || !text.trim()) throw new Error(`${label} stream ended before response.completed.`);
  return text;
}

function chatMessages(payload) {
  return [
    ...(payload.history || []).map(item => ({ role: item.role === "assistant" ? "assistant" : "user", content: item.text })),
    { role: "user", content: payload.prompt },
  ];
}

async function inferApi(payload) {
  const credentials = readApiCredential();
  const provider = credentials.provider || "openai";
  const providerConfig = apiProviders[provider];
  if (!providerConfig) throw new Error("api_unavailable");
  const providerModel = credentials.model || providerConfig.model;
  const headers = apiKeyHeaders(provider, credentials.apiKey);
  const messages = chatMessages(payload);
  let response;
  if (provider === "openai") {
    const input = messages.map(item => ({ role: item.role, content: item.content }));
    response = await fetch("https://api.openai.com/v1/responses", { method: "POST", headers, body: JSON.stringify({ model: providerModel, instructions: payload.instructions, input, store: false, stream: true }), redirect: "error", signal: AbortSignal.timeout(120_000) });
    return responseText(response, "OpenAI API");
  }
  if (provider === "gemini") {
    const contents = messages.map(item => ({ role: item.role === "assistant" ? "model" : "user", parts: [{ text: item.content }] }));
    response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(providerModel)}:generateContent`, {
      method: "POST", headers, body: JSON.stringify({ systemInstruction: { parts: [{ text: payload.instructions }] }, contents, generationConfig: { maxOutputTokens: 4096 } }), redirect: "error", signal: AbortSignal.timeout(120_000),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) apiError(response, JSON.stringify(result));
    return (result.candidates?.[0]?.content?.parts || []).map(part => part.text || "").join("").trim();
  }
  if (provider === "claude") {
    response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST", headers, body: JSON.stringify({ model: providerModel, system: payload.instructions, messages, max_tokens: 4096 }), redirect: "error", signal: AbortSignal.timeout(120_000),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) apiError(response, JSON.stringify(result));
    return (result.content || []).filter(part => part.type === "text").map(part => part.text || "").join("").trim();
  }
  response = await fetch("https://api.deepseek.com/chat/completions", {
    method: "POST", headers, body: JSON.stringify({ model: providerModel, messages: [{ role: "system", content: payload.instructions }, ...messages], stream: false }), redirect: "error", signal: AbortSignal.timeout(120_000),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) apiError(response, JSON.stringify(result));
  return String(result.choices?.[0]?.message?.content || "").trim();
}

async function infer(payload, useApiKey = false) {
  if (useApiKey) {
    const text = await inferApi(payload);
    if (!text) throw new Error("api_unavailable");
    process.stdout.write(JSON.stringify({ text }));
    return;
  }
  const token = (await getAccessToken()).accessToken;
  const input = chatMessages(payload).map(item => ({ role: item.role, content: item.content }));
  const response = await fetch("https://api.openai.com/v1/responses", { method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" }, body: JSON.stringify({ model, instructions: payload.instructions, input, store: false, stream: true }), redirect: "error", signal: AbortSignal.timeout(120_000) });
  const text = await responseText(response, "ChatGPT plan");
  process.stdout.write(JSON.stringify({ text }));
}

if (command === "login") await login();
else if (command === "login-new") await login(true);
else if (command === "login-telegram") await login(false, true);
else if (command === "login-max") await login(false, true);
else if (command === "login-vk") await login(false, true);
else if (["api-login", "api-login-max", "api-login-vk"].includes(command)) await apiLogin(process.argv[4] || "");
else if (command === "status") {
  try { const c = loadCredentials(); console.log(JSON.stringify({ connected: Boolean(c.accessToken && c.scopes.includes("chatgpt.tokens.use.direct") && (c.expiresAt > Date.now() + 60_000 || c.refreshToken)), email: c.email })); }
  catch { console.log(JSON.stringify({ connected: false })); }
} else if (["infer", "infer-api", "connect-api", "infer-max", "infer-api-max", "connect-api-max", "infer-vk", "infer-api-vk", "connect-api-vk"].includes(command)) {
  let input = ""; for await (const chunk of process.stdin) input += chunk;
  try {
    if (accountCommand && !telegramId) throw new Error("missing_account");
    const payload = JSON.parse(input);
    if (["connect-api", "connect-api-max", "connect-api-vk"].includes(command)) await connectApiKey(payload.apiKey, payload.provider);
    else await infer(payload, ["infer-api", "infer-api-max", "infer-api-vk"].includes(command));
  } catch (error) {
    const safeCode = /^(invalid_key|api_http_[0-9]{3})$/.test(error.message) ? error.message : "api_unavailable";
    if (command === "infer-vk") {
      const detail = String(error?.message || "");
      let code = "api_unavailable";
      if (/ChatGPT ещё не подключён|missing_account/i.test(detail)) code = "chatgpt_not_connected";
      else if (/invalid_grant|refresh token|token.*expired|401/i.test(detail)) code = "chatgpt_session_expired";
      else if (/scope|plan.*разреш|permission|403/i.test(detail)) code = "chatgpt_access_denied";
      else if (/model_not_found|unsupported_model|model.*not found|404/i.test(detail)) code = "chatgpt_model_unavailable";
      else if (/fetch failed|ECONN|ETIMEDOUT|ENOTFOUND|network|UND_ERR|SSL/i.test(detail)) code = "openai_network_error";
      process.stderr.write(code);
    } else {
      process.stderr.write(["api-login", "api-login-max", "api-login-vk", "connect-api", "connect-api-max", "connect-api-vk", "infer-api", "infer-api-max", "infer-api-vk"].includes(command) ? safeCode : error.message);
    }
    process.exitCode = 1;
  }
} else { console.error("Use: node siwc.mjs login | status | infer | connect-api | infer-api | login-max | infer-max | infer-api-max | connect-api-max | login-vk | infer-vk | infer-api-vk | connect-api-vk"); process.exitCode = 2; }
