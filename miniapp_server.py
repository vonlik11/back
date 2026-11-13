import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def telegram_user(init_data, token):
    if not token or not isinstance(init_data, str) or len(init_data) > 8192:
        raise RequestError(401, "Откройте приложение заново через Telegram.")
    try:
        pairs = parse_qsl(init_data, strict_parsing=True, max_num_fields=32)
        fields = dict(pairs)
        supplied = fields.pop("hash")
        check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.digest(b"WebAppData", token.encode(), "sha256")
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        age = time.time() - int(fields["auth_date"])
        if len(fields) + 1 != len(pairs) or not re.fullmatch("[a-f0-9]{64}", supplied) or not hmac.compare_digest(expected, supplied) or not -60 <= age <= 900:
            raise ValueError
        user_id = json.loads(fields["user"])["id"]
        if type(user_id) is not int or not 0 < user_id < 2**52:
            raise ValueError
        return str(user_id)
    except (ValueError, KeyError, TypeError):
        raise RequestError(401, "Откройте приложение заново через Telegram.") from None


def start_server(bot, port):
    assets = bot.ROOT / "miniapp"
    sessions = {}
    attempts = {}
    lifetime = 8 * 3600

    def dashboard(chat_id):
        workspace = bot.workspaces[chat_id]
        profile = workspace["profile"]
        account = workspace["registration"]
        posts = [
            {
                "id": draft_id, "text": draft["text"], "status": draft["status"],
                "photo": bool(draft.get("photo_id")), "published_at": draft.get("published_at"),
                "error": draft.get("publish_error", ""),
                "photo_source": draft.get("photo_source"),
            }
            for draft_id, draft in reversed(list(bot.state["drafts"].items()))
            if draft.get("chat_id") == chat_id and draft["status"] != "superseded"
        ]
        return {
            "name": account["display_name"], "email": account.get("email", ""),
            "company": profile.get("name", ""), "description": profile.get("description", ""),
            "channel": workspace.get("channel_label", workspace["channel"]),
            "provider": bot.ai_provider(chat_id), "schedule": workspace.get("schedule"),
            "timezone": bot.TIMEZONE, "posts": posts,
            "analytics": bot.user_database.analytics(chat_id),
        }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status, body, media_type, cookie=None):
            self.send_response(status)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def json_reply(self, status, data, cookie=None):
            self.reply(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", cookie)

        def body(self):
            if self.headers.get_content_type() != "application/json":
                raise RequestError(415, "Ожидается JSON.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 14 * 1024 * 1024:
                raise RequestError(413, "Запрос слишком большой.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError
            return data

        def session_token(self):
            jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return jar["mark_session"].value if "mark_session" in jar else ""

        def cookie(self, value, age):
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return f"mark_session={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}{secure}"

        def account(self):
            session = sessions.get(self.session_token())
            if session:
                chat_id, registration_date, expires = session
                registration = bot.workspaces.get(chat_id, {}).get("registration", {})
                if expires >= time.time() and registration and registration.get("registered_at") == registration_date:
                    return chat_id
            raise RequestError(401, "Войдите в свой аккаунт.")

        def login(self, data):
            init_data = data.get("init_data")
            if init_data:
                chat_id = telegram_user(init_data, bot.BOT_TOKEN)
            else:
                now = time.monotonic()
                for address, (_, until) in list(attempts.items()):
                    if until <= now:
                        del attempts[address]
                address = self.client_address[0]
                count, until = attempts.get(address, (0, now + 300))
                if count >= 10:
                    raise RequestError(429, "Слишком много попыток входа. Повторите через несколько минут.")
                email = data.get("email", "")
                password = data.get("password", "")
                chat_id = bot.user_database.authenticate(email, password)
                if not chat_id:
                    attempts[address] = (count + 1, until)
                    raise RequestError(401, "Проверьте email и пароль от аккаунта бота.")
                attempts.pop(address, None)
            registration = bot.workspaces.get(chat_id, {}).get("registration", {})
            if not registration:
                raise RequestError(403, "Сначала завершите регистрацию в Telegram-боте командой /start.")
            now = time.time()
            for key, (_, _, expires) in list(sessions.items()):
                if expires < now:
                    del sessions[key]
            token = secrets.token_urlsafe(32)
            sessions[token] = (chat_id, registration.get("registered_at"), now + lifetime)
            return dashboard(chat_id), self.cookie(token, lifetime)

        def post(self, path, data):
            if path == "/api/login":
                return self.login(data)
            if path == "/api/logout":
                sessions.pop(self.session_token(), None)
                return {}, self.cookie("", 0)
            chat_id = self.account()
            workspace = bot.workspaces[chat_id]
            if path == "/api/connect":
                key = data.get("key")
                if not isinstance(key, str):
                    raise ValueError
                bot.save_openai_key(chat_id, key.strip())
            elif path == "/api/schedule":
                schedule = data.get("time")
                if schedule is None:
                    workspace.pop("schedule", None)
                elif isinstance(schedule, str) and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", schedule):
                    workspace["schedule"] = schedule
                else:
                    raise RequestError(400, "Выберите время в формате ЧЧ:ММ.")
                bot.write_json(bot.STATE_FILE, bot.state)
            elif path == "/api/profile":
                name, description = data.get("company"), data.get("description")
                if not isinstance(name, str) or not isinstance(description, str) or not name.strip() or len(name) > 150 or len(description) > 2000:
                    raise RequestError(400, "Заполните название и описание компании.")
                bot.user_database.update_company(chat_id, name.strip(), description.strip())
                workspace["profile"].update(name=name.strip(), description=description.strip())
                bot.write_json(bot.STATE_FILE, bot.state)
            elif path == "/api/posts":
                topic = data.get("topic")
                if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 3000:
                    raise RequestError(400, "Введите тему поста длиной до 3000 символов.")
                photo = data.get("photo")
                image = None
                if photo:
                    image = base64.b64decode(photo, validate=True)
                    if len(image) > 10 * 1024 * 1024 or not image.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
                        raise RequestError(400, "Выберите изображение JPEG или PNG до 10 МБ.")
                text = bot.openai_text(chat_id, topic.strip(), post=True)
                if image:
                    previous = bot.pending_for(chat_id)
                    bot.attach_image_bytes(chat_id, {"channel": workspace["channel"], "text": text}, image, {"kind": "user"})
                    if previous:
                        previous["status"] = "superseded"
                        bot.write_json(bot.STATE_FILE, bot.state)
                        bot.clear_draft_buttons(previous)
                else:
                    request = topic if data.get("illustrate") else "только текст"
                    bot.prepare_post(chat_id, text, request, replaces=bot.pending_for(chat_id))
            else:
                match = re.fullmatch(r"/api/posts/([a-f0-9]{32})/(edit|publish|reject|image|webimage|noimage)", path)
                if not match:
                    raise RequestError(404, "Действие не найдено.")
                draft_id, action = match.groups()
                draft = bot.state["drafts"].get(draft_id)
                if not draft or draft.get("chat_id") != chat_id:
                    raise RequestError(404, "Черновик не найден.")
                if draft["status"] != "pending":
                    raise RequestError(409, "Этот черновик уже обработан.")
                if action == "publish":
                    job_id = bot.start_publish_job(chat_id, draft_id, notify_user=True)
                    return {"job_id": job_id}, None
                elif action == "reject":
                    draft["status"] = "rejected"
                    bot.write_json(bot.STATE_FILE, bot.state)
                    bot.clear_draft_buttons(draft)
                elif action == "edit":
                    text = data.get("text", "")
                    limit = 1024 if draft.get("photo_id") else 3500
                    if not isinstance(text, str) or not text.strip() or bot.text_length(text) > limit:
                        raise RequestError(400, f"Введите текст длиной до {limit} символов.")
                    bot.send_draft(chat_id, text.strip(), replaces=draft, photo_id=draft.get("photo_id"), photo_source=draft.get("photo_source"))
                elif action == "image":
                    bot.add_draft_image(chat_id, draft)
                elif action == "webimage":
                    bot.add_web_image(chat_id, draft, fallback=False)
                else:
                    bot.send_draft(chat_id, draft["text"], replaces=draft)
            return dashboard(chat_id), None

        def do_GET(self):
            path = urlsplit(self.path).path
            static = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/styles.css": ("styles.css", "text/css")}
            try:
                if path in static:
                    name, media_type = static[path]
                    self.reply(200, (assets / name).read_bytes(), f"{media_type}; charset=utf-8")
                    return
                if path == "/api/config":
                    self.json_reply(200, {"bot": bot.BOT_USERNAME})
                    return
                photo = re.fullmatch(r"/api/posts/([a-f0-9]{32})/photo", path)
                job = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", path)
                with bot.bot_state_lock:
                    chat_id = self.account()
                    if path == "/api/dashboard":
                        result = dashboard(chat_id)
                    elif job:
                        try:
                            result = {**bot.publish_job_status(chat_id, job[1]), "dashboard": dashboard(chat_id)}
                        except bot.SetupError:
                            raise RequestError(404, "Задача публикации не найдена.") from None
                    elif photo:
                        draft = bot.state["drafts"].get(photo[1], {})
                        if draft.get("chat_id") != chat_id or not draft.get("photo_id"):
                            raise RequestError(404, "Фото не найдено.")
                        file_id = draft["photo_id"]
                    else:
                        raise RequestError(404, "Страница не найдена.")
                if photo:
                    image = bot.download_user_image({"file_id": file_id})
                    self.reply(200, image, "image/png" if image.startswith(b"\x89PNG") else "image/jpeg")
                else:
                    self.json_reply(200, result)
            except RequestError as exc:
                self.json_reply(exc.status, {"error": str(exc)})
            except (bot.SetupError, bot.TelegramError, OSError):
                self.json_reply(502, {"error": "Не удалось загрузить данные."})

        def do_POST(self):
            try:
                if self.headers.get("X-Mark-App") != "1":
                    raise RequestError(403, "Откройте приложение заново.")
                data = self.body()
                with bot.bot_state_lock:
                    result, cookie = self.post(urlsplit(self.path).path, data)
                self.json_reply(200, result, cookie)
            except RequestError as exc:
                self.json_reply(exc.status, {"error": str(exc)})
            except (bot.SetupError, bot.ImageError, bot.WebImageError) as exc:
                self.json_reply(422, {"error": str(exc)})
            except (ValueError, TypeError):
                self.json_reply(400, {"error": "Проверьте введённые данные."})
            except Exception:
                self.json_reply(502, {"error": "Не удалось выполнить действие. Обновите страницу перед повтором."})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
