"""MAX адаптер AI-маркетолога: текстовый черновик -> согласование -> канал."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from image_provider import ImageError, configured as images_configured, generate_image
from web_images import WebImageError, search_images, download_image

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "max_state.json"
API_ROOT = "https://platform-api2.max.ru"


def node_command() -> str:
    """Find Node.js even when PyCharm's PATH differs from the system PATH."""
    configured = os.getenv("NODE_EXECUTABLE")
    candidate = configured or shutil.which("node") or shutil.which("node.exe")
    if candidate:
        return candidate
    if os.name == "nt":
        for candidate in (r"C:\Program Files\nodejs\node.exe", os.path.expandvars(r"%LOCALAPPDATA%\Programs\nodejs\node.exe")):
            if candidate and Path(candidate).is_file():
                return candidate
    raise RuntimeError("Node.js не найден. Установите Node.js 22.12+ или укажите NODE_EXECUTABLE в .env, затем перезапустите PyCharm.")


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()
TOKEN = os.getenv("MAX_BOT_TOKEN", "")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Yekaterinburg")
DAILY_AI_LIMIT = max(1, int(os.getenv("DAILY_AI_LIMIT", "30")))
DAILY_USER_LIMIT = max(1, int(os.getenv("DAILY_USER_LIMIT", "5")))
DAILY_IMAGE_LIMIT = max(1, int(os.getenv("DAILY_IMAGE_LIMIT", "10")))
DAILY_USER_IMAGE_LIMIT = max(1, int(os.getenv("DAILY_USER_IMAGE_LIMIT", "3")))
try:
    LOCAL_TZ = ZoneInfo(TIMEZONE)
except ZoneInfoNotFoundError:
    TIMEZONE = "UTC"
    LOCAL_TZ = ZoneInfo("UTC")


def request_json(url: str, method="GET", body=None, headers=None, timeout=45):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            result = json.loads(raw.decode()) if raw else {}
            if isinstance(result, dict) and result.get("success") is False:
                code = result.get("code", "max.error")
                message = result.get("message", "запрос завершился ошибкой")
                raise RuntimeError(f"MAX API {code}: {message}")
            return result
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def max_api(path: str, method="GET", params=None, body=None):
    query = urllib.parse.urlencode(params or {})
    url = f"{API_ROOT}{path}" + (f"?{query}" if query else "")
    headers = {"Authorization": TOKEN, "Content-Type": "application/json"}
    return request_json(url, method, body, headers)


def load_state():
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"drafts": {}, "marker": None}
    except (OSError, json.JSONDecodeError):
        return {"drafts": {}, "marker": None}


state = load_state()
state.setdefault("companies", {})
state.setdefault("channels", {})
state.setdefault("pending_channels", {})
state.setdefault("registration", {})
state.setdefault("drafts", {})
state.setdefault("schedules", {})
state.setdefault("schedule_runs", {})
state.setdefault("ai_usage", {})
state.setdefault("image_usage", {})


def save_state():
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def keyboard(rows):
    return [{"type": "inline_keyboard", "payload": {"buttons": rows}}]


def callback_button(label, payload):
    return {"type": "callback", "text": label, "payload": payload}


def upload_image_bytes(image: bytes) -> str:
    upload = max_api("/uploads", method="POST", params={"type": "image"})
    upload_url = upload.get("url")
    token = upload.get("token")
    if not upload_url or not token:
        raise RuntimeError("MAX не вернул ссылку для загрузки изображения.")
    boundary = "----CodexMax" + uuid.uuid4().hex
    extension, mime = ("png", "image/png") if image.startswith(b"\x89PNG\r\n\x1a\n") else ("jpg", "image/jpeg")
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"data\"; filename=\"post.{extension}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("ascii") + image + f"\r\n--{boundary}--\r\n".encode("ascii")
    req = urllib.request.Request(upload_url, data=body, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            result = response.read(1000).decode("utf-8", "replace")
            if response.status >= 300 or "retval>1<" not in result:
                raise RuntimeError("MAX не подтвердил загрузку изображения.")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Загрузка изображения завершилась ошибкой HTTP {exc.code}.") from exc
    time.sleep(2)
    return token


def send_text(user_id, text, buttons=None, image_token=None):
    body = {"text": text[:4000]}
    attachments = []
    if image_token:
        attachments.append({"type": "image", "payload": {"token": image_token}})
    if buttons:
        attachments.extend(keyboard(buttons))
    if attachments:
        body["attachments"] = attachments
    return max_api("/messages", "POST", {"user_id": int(user_id)}, body)


def send_channel(user_id: str, text: str, image_token=None, source_url=None):
    channel_id = state.get("channels", {}).get(user_id)
    if not channel_id:
        raise RuntimeError("Канал ещё не подключён. Добавьте бота в канал администратором с правом публикации.")
    member = max_api(f"/chats/{channel_id}/members/me")
    permissions = set(member.get("permissions") or [])
    if not member.get("is_admin") or not ({"write", "post_edit_delete_message"} & permissions):
        state["channels"].pop(user_id, None)
        save_state()
        raise RuntimeError("У бота нет права публикации. Выдайте право «Публиковать сообщения» и добавьте его в канал снова.")
    body = {"text": text[:4000]}
    attachments = []
    if image_token:
        attachments.append({"type": "image", "payload": {"token": image_token}})
    if source_url and source_url.startswith("https://commons.wikimedia.org/"):
        body["text"] = (body["text"] + f"\n\nИллюстрация: {source_url}")[:4000]
    if attachments:
        body["attachments"] = attachments
    return max_api("/messages", "POST", {"chat_id": channel_id}, body)


def chatgpt_connected(user_id: str) -> bool:
    auth_dir = ROOT / ".auth" / "max" / user_id
    return (auth_dir / "chatgpt.enc").is_file() or (auth_dir / "api-key.enc").is_file()


def ai_provider(user_id: str) -> str:
    provider_file = ROOT / ".auth" / "max" / user_id / "provider"
    try:
        provider = provider_file.read_text(encoding="utf-8").strip()
        return provider if provider in {"chatgpt", "api"} else "chatgpt"
    except OSError:
        return "api" if (ROOT / ".auth" / "max" / user_id / "api-key.enc").is_file() else "chatgpt"


def local_date() -> str:
    return datetime.now(LOCAL_TZ).date().isoformat()


def reserve_ai_request(user_id: str, *, image=False):
    field = "image_usage" if image else "ai_usage"
    record = state.setdefault(field, {})
    today = local_date()
    day = record.setdefault(today, {"total": 0, "users": {}})
    total_limit = DAILY_IMAGE_LIMIT if image else DAILY_AI_LIMIT
    user_limit = DAILY_USER_IMAGE_LIMIT if image else DAILY_USER_LIMIT
    user_count = int(day["users"].get(user_id, 0))
    if int(day.get("total", 0)) >= total_limit or user_count >= user_limit:
        kind = "картинок" if image else "AI-запросов"
        raise RuntimeError(f"Дневной лимит {kind} исчерпан. Попробуйте завтра.")
    day["total"] = int(day.get("total", 0)) + 1
    day["users"][user_id] = user_count + 1
    for old_day in list(record):
        if old_day < today and old_day not in {today}:
            record.pop(old_day, None)
    save_state()


def ai_text(user_id: str, prompt: str, instructions: str, *, reserve=True) -> str:
    if reserve:
        reserve_ai_request(user_id)
    command = "infer-api-max" if ai_provider(user_id) == "api" else "infer-max"
    result = subprocess.run(
        [node_command(), "siwc.mjs", command, user_id], cwd=ROOT,
        input=json.dumps({"prompt": prompt, "instructions": instructions}, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", timeout=150,
    )
    if result.returncode:
        raise RuntimeError("AI не ответил. Переподключите аккаунт командой /chatgpt.")
    text = json.loads(result.stdout).get("text", "").strip()
    if not text:
        raise RuntimeError("Модель вернула пустой ответ")
    return text


def generate_post(user_id: str, prompt: str) -> str:
    company = state.get("companies", {}).get(user_id, {})
    profile_text = json.dumps(company, ensure_ascii=False)
    instructions = (
        "Ты маркетолог компании. Подготовь готовый пост для MAX на русском языке. "
        "Пиши ясно и естественно, без выдуманных фактов, цен, обещаний и статистики. "
        "Если данных не хватает, задай уточняющий вопрос вместо публикации. "
        "Верни только текст публикации, без пояснений.\n"
        f"Профиль компании: {profile_text}\nЗадача: {prompt}"
    )
    return ai_text(user_id, prompt, instructions)[:3500]


def get_web_image(user_id: str, prompt: str):
    query = ai_text(user_id, prompt, "Извлеки короткую поисковую фразу на русском или английском, подходящую для поиска лицензированного изображения. Верни только фразу, до 8 слов.")
    candidates = search_images(query)
    if not candidates:
        return None, None
    # Prefer the first public-domain/CC0 result; include its source with the post.
    selected = candidates[0]
    return upload_image_bytes(download_image(selected["download_url"])), selected["url"]


def get_generated_image(user_id: str, prompt: str):
    reserve_ai_request(user_id, image=True)
    image = generate_image(prompt, user_id)
    return upload_image_bytes(image), None


def draft_buttons(draft_id: str, has_image=False):
    rows = [
        [callback_button("✅ Одобрить и опубликовать", f"approve:{draft_id}")],
        [callback_button("❌ Отклонить", f"reject:{draft_id}")],
        [callback_button("🔎 Найти фото", f"webimage:{draft_id}"), callback_button("🎨 Создать картинку", f"image:{draft_id}")],
    ]
    if has_image:
        rows.append([callback_button("📝 Оставить только текст", f"noimage:{draft_id}")])
    return rows


def show_draft(user_id: str, draft: dict, note=""):
    draft_id = draft.get("id") or next((key for key, value in state.get("drafts", {}).items() if value is draft), "")
    draft["id"] = draft_id
    source = f"\nИсточник изображения: {draft['source_url']}" if draft.get("source_url") else ""
    caption = f"📝 Черновик для MAX-канала\nСтатус: ждёт вашего решения\n\n{draft['text']}{source}\n\nПроверьте текст и изображение. Можно одобрить, отклонить или изменить: «переделай: …»."
    if note:
        caption = note + "\n\n" + caption
    send_text(user_id, caption, draft_buttons(draft["id"], bool(draft.get("image_token"))), draft.get("image_token"))


def get_owner(update):
    msg = update.get("message", {})
    sender = msg.get("sender", {})
    return str(sender.get("user_id") or update.get("user", {}).get("user_id") or "")


def connect_user_channel(user_id: str, channel_id: str):
    member = max_api(f"/chats/{channel_id}/members/me")
    permissions = set(member.get("permissions") or [])
    if not member.get("is_admin") or not ({"write", "post_edit_delete_message"} & permissions):
        send_text(user_id, "Канал найден, но у бота нет права публикации. Выдайте право «Публиковать сообщения» и добавьте бота снова.")
        return False
    if state.get("companies", {}).get(user_id):
        state.setdefault("channels", {})[user_id] = str(channel_id)
        state.setdefault("pending_channels", {}).pop(user_id, None)
        save_state()
        send_text(user_id, f"✅ Ваш MAX-канал подключён автоматически. ID: {channel_id}.")
    else:
        state.setdefault("pending_channels", {})[user_id] = str(channel_id)
        save_state()
    return True


def handle_bot_added(update):
    if not update.get("is_channel") or not update.get("chat_id"):
        return
    user_id = str((update.get("user") or {}).get("user_id", ""))
    if not user_id:
        return
    channel_id = str(update["chat_id"])
    try:
        connect_user_channel(user_id, channel_id)
    except Exception as exc:
        send_text(user_id, f"Канал обнаружен, но не удалось проверить права бота: {str(exc)[:180]}")


def greet(user_id: str):
    buttons = [
        [callback_button("✍️ Создать пост", "create_post"), callback_button("👤 Мой профиль", "profile")],
        [callback_button("📊 Публикации", "analytics"), callback_button("⏰ Расписание", "schedule")],
        [callback_button("❓ Помощь", "help")],
    ]
    send_text(user_id,
        "Здравствуйте! Я AI-маркетолог. Ваш профиль и канал будут храниться отдельно от других пользователей.\n\n"
        "Напишите задачу обычным сообщением, например: «Подготовь пост о новой услуге». "
        "Я пришлю черновик. В канал он попадёт только после вашего одобрения.\n\n"
        "Команды: /id, /chatgpt, /company, /channel, /schedule, /analytics, /help.", buttons)


def begin_registration(user_id: str):
    forms = state.setdefault("registration", {})
    form = forms.get(user_id)
    if not form:
        form = {"step": "name"}
        forms[user_id] = form
        save_state()
    if form.get("step") == "description":
        send_text(user_id, "Продолжим регистрацию. Чем занимается компания? Напишите, что она предлагает, кто её клиенты и какой стиль общения предпочитает.")
    else:
        send_text(user_id, "Давайте настроим AI-маркетолога.\n\nКак называется ваша компания?")


def handle_registration(user_id: str, text: str):
    form = state.get("registration", {}).get(user_id)
    if not form:
        return False
    value = text.strip()
    if not value or len(value) > (120 if form["step"] == "name" else 2000):
        send_text(user_id, "Пришлите непустой ответ подходящей длины.")
        return True
    if form["step"] == "name":
        form["company_name"] = value
        form["step"] = "description"
        save_state()
        send_text(user_id, "Чем занимается компания? Напишите, что она предлагает, кто её клиенты и какой стиль общения предпочитает.")
        return True
    state.setdefault("companies", {})[user_id] = {
        "name": form["company_name"], "description": value,
    }
    del state["registration"][user_id]
    save_state()
    pending_channel = state.get("pending_channels", {}).get(user_id)
    if pending_channel:
        try:
            connect_user_channel(user_id, pending_channel)
        except Exception as exc:
            send_text(user_id, f"Профиль сохранён, но канал не удалось подключить: {str(exc)[:180]}")
    send_text(user_id,
        f"✅ Профиль компании сохранён.\nКомпания: {form['company_name']}\n\n"
        "Теперь подключите ChatGPT и канал. Канал найдётся автоматически после добавления бота администратором.",
        [[callback_button("🔐 Подключить ChatGPT", "connect_chatgpt")],
         [callback_button("📣 Подключить канал", "connect_channel")],
         [callback_button("✍️ Создать пост", "create_post"), callback_button("👤 Мой профиль", "profile")],
         [callback_button("📊 Публикации", "analytics"), callback_button("⏰ Расписание", "schedule")]],
    )


def start_chatgpt_login(user_id: str):
    try:
        login = subprocess.Popen(
            [node_command(), "siwc.mjs", "login-max", user_id], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        )
    except (OSError, RuntimeError) as exc:
        send_text(user_id, str(exc))
        return
    try:
        info = json.loads(login.stdout.readline())
        send_text(user_id,
            "Откройте ссылку, войдите в ChatGPT и разрешите использовать возможности своего плана. "
            "После завершения входа вернитесь сюда.",
            [[{"type": "link", "text": "Войти через ChatGPT", "url": info["url"]}]],
        )
    except Exception:
        login.terminate()
        send_text(user_id, "Не получилось создать ссылку входа. Проверьте npm install и повторите /chatgpt.")
        return

    def finish():
        try:
            code = login.wait(timeout=660)
            if code == 0 and chatgpt_connected(user_id):
                send_text(user_id, "✅ ChatGPT подключён. Выберите действие в меню или напишите задачу для поста.", [
                    [callback_button("✍️ Создать пост", "create_post"), callback_button("👤 Мой профиль", "profile")],
                    [callback_button("📊 Публикации", "analytics"), callback_button("⏰ Расписание", "schedule")],
                ])
            else:
                send_text(user_id, "Вход не завершился. Попробуйте /chatgpt ещё раз.")
        except subprocess.TimeoutExpired:
            login.terminate()
            send_text(user_id, "Ссылка истекла. Для нового входа отправьте /chatgpt.")
        finally:
            if login.stdout:
                login.stdout.close()
    threading.Thread(target=finish, daemon=True).start()


def create_draft(user_id: str, prompt: str, image_token=None):
    send_text(user_id, "Готовлю черновик. Это может занять несколько секунд…")
    text = generate_post(user_id, prompt)
    source_url = None
    if not image_token and not re.search(r"\b(только текст|без картинки|без фото)\b", prompt, re.IGNORECASE):
        try:
            image_token, source_url = get_web_image(user_id, prompt)
        except Exception as exc:
            print(f"Image search skipped: {exc}")
    for draft in state["drafts"].values():
        if draft.get("owner_id") == user_id and draft.get("status") == "pending":
            draft["status"] = "superseded"
    draft_id = uuid.uuid4().hex[:16]
    draft = {"id": draft_id, "owner_id": user_id, "text": text, "status": "pending", "created_at": int(time.time())}
    if image_token:
        draft["image_token"] = image_token
        if source_url:
            draft["source_url"] = source_url
    state["drafts"][draft_id] = draft
    save_state()
    show_draft(user_id, draft)


def pending_draft(user_id: str):
    return next((d for d in reversed(list(state["drafts"].values()))
                 if d.get("owner_id") == user_id and d.get("status") in {"pending", "needs_review"}), None)


def profile_text(user_id: str):
    company = state.get("companies", {}).get(user_id, {})
    channel_id = state.get("channels", {}).get(user_id, "не подключён")
    return f"Компания: {company.get('name', '—')}\nОписание: {company.get('description', '—')}\nКанал ID: {channel_id}\nAI: {'подключён' if chatgpt_connected(user_id) else 'не подключён'}"


def publication_summary(user_id: str):
    posts = [d for d in state.get("drafts", {}).values() if d.get("owner_id") == user_id and d.get("status") == "published"]
    posts.sort(key=lambda d: d.get("published_at", 0), reverse=True)
    lines = [f"Опубликовано ботом: {len(posts)} постов.", "Последние публикации:"]
    for draft in posts[:10]:
        when = datetime.fromtimestamp(draft.get("published_at", 0), LOCAL_TZ).strftime("%d.%m %H:%M") if draft.get("published_at") else "дата неизвестна"
        short = draft.get("text", "").replace("\n", " ")[:90]
        lines.append(f"• {when} — {short}")
    if not posts:
        lines.append("Публикаций пока нет.")
    lines.append("MAX API сейчас не предоставляет боту счётчики реакций для этого отчёта.")
    return "\n".join(lines)


def schedule_command(user_id: str, text: str):
    parts = text.split(maxsplit=1)
    current = state.get("schedules", {}).get(user_id)
    if len(parts) == 1:
        send_text(user_id, f"Расписание: {current or 'выключено'} ({TIMEZONE}).\n/schedule 09:00 — ежедневный черновик\n/schedule off — выключить.")
        return
    value = parts[1].strip().lower()
    if value == "off":
        state["schedules"].pop(user_id, None)
        state["schedule_runs"].pop(user_id, None)
        save_state()
        send_text(user_id, "Ежедневная подготовка черновика выключена.")
        return
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        send_text(user_id, "Укажите время в формате ЧЧ:ММ, например /schedule 09:00.")
        return
    state.setdefault("schedules", {})[user_id] = value
    save_state()
    send_text(user_id, f"Готово. Каждый день в {value} ({TIMEZONE}) я подготовлю черновик для согласования. Автоматической публикации не будет.")


def scheduled_job():
    while True:
        now = datetime.now(LOCAL_TZ)
        hhmm = now.strftime("%H:%M")
        today = now.date().isoformat()
        for user_id, at in list(state.get("schedules", {}).items()):
            if at != hhmm or state.get("schedule_runs", {}).get(user_id) == today:
                continue
            state.setdefault("schedule_runs", {})[user_id] = today
            save_state()
            if state.get("companies", {}).get(user_id) and state.get("channels", {}).get(user_id) and chatgpt_connected(user_id):
                company = state["companies"][user_id]
                try:
                    create_draft(user_id, f"Подготовь ежедневный полезный пост для компании {company.get('name', '')}. Опирайся только на профиль компании; если темы недостаточно — попроси уточнение.")
                except Exception as exc:
                    print(f"Scheduled draft failed for {user_id}: {exc}")
                    try:
                        send_text(user_id, f"Не удалось подготовить запланированный черновик: {str(exc)[:180]}")
                    except Exception:
                        pass
        time.sleep(20)


def handle_message(update):
    user_id = get_owner(update)
    if not user_id:
        return
    message = update.get("message", {})
    body = message.get("body") or {}
    text = (body.get("text") or "").strip()
    image_attachment = next((a for a in body.get("attachments", []) if a.get("type") == "image"), None)
    image_payload = (image_attachment or {}).get("payload") or {}
    incoming_image = image_payload.get("token")
    if text == "/id":
        send_text(user_id, f"Ваш MAX user ID: {user_id}.")
        return
    if text in ("/start", "/help"):
        if text == "/start" and not state.get("companies", {}).get(user_id):
            begin_registration(user_id)
        else:
            greet(user_id)
        return
    if text == "/company":
        begin_registration(user_id)
        return
    if text == "/profile":
        company = state.get("companies", {}).get(user_id)
        if company:
            send_text(user_id, profile_text(user_id) + "\n\nИзменить профиль: /company")
        else:
            begin_registration(user_id)
        return
    if text == "/analytics":
        send_text(user_id, publication_summary(user_id))
        return
    if text.startswith("/schedule"):
        schedule_command(user_id, text)
        return
    if text == "/status":
        draft = pending_draft(user_id)
        send_text(user_id, f"Статус черновика: {draft.get('status')}.\n\n{draft.get('text', '')}" if draft else "Ожидающего черновика нет.")
        return
    if text == "/reset":
        send_text(user_id, "Сбросить профиль, подключение ChatGPT, канал и ожидающие черновики? Опубликованные посты останутся в канале.", [[callback_button("Подтвердить сброс", "reset_confirm"), callback_button("Отмена", "reset_cancel")]])
        return
    if text == "/channel":
        send_text(user_id,
            "Добавьте этого бота в нужный MAX-канал как администратора с правом публикации. "
            "MAX сам передаст боту ID канала, и он подтвердит подключение здесь."
        )
        return
    if text == "/chatgpt":
        start_chatgpt_login(user_id)
        return
    if text == "/cancel":
        state.get("registration", {}).pop(user_id, None)
        state.get("uploads", {}).pop(user_id, None)
        save_state()
        send_text(user_id, "Текущий шаг настройки отменён. Профиль и черновики сохранены.")
        return
    if handle_registration(user_id, text):
        return
    if not state.get("companies", {}).get(user_id):
        begin_registration(user_id)
        return
    if not state.get("channels", {}).get(user_id):
        send_text(user_id, "Сначала подключите MAX-канал: отправьте /channel и добавьте бота администратором с правом публикации.")
        return
    if not chatgpt_connected(user_id):
        send_text(user_id, "Сначала подключите ChatGPT, чтобы готовить посты.", [[callback_button("🔐 Подключить ChatGPT", "connect_chatgpt")]])
        return
    if not text and incoming_image:
        state.setdefault("uploads", {})[user_id] = incoming_image
        save_state()
        send_text(user_id, "Фото сохранено для следующего поста. Теперь отправьте тему текстом или пришлите фото с подписью.")
        return
    if not text and not incoming_image:
        send_text(user_id, "Напишите тему поста или прикрепите фото с подписью.")
        return
    command, _, command_arg = text.partition(" ")
    command = command.lower()
    if command in {"/image", "/findimage"}:
        draft = pending_draft(user_id)
        if not draft:
            send_text(user_id, "Сначала подготовьте черновик, затем используйте /image или /findimage.")
            return
        try:
            if command == "/image":
                draft["image_token"], draft["source_url"] = get_generated_image(user_id, command_arg.strip() or draft["text"])
            else:
                draft["image_token"], draft["source_url"] = get_web_image(user_id, command_arg.strip() or draft["text"])
            save_state()
            show_draft(user_id, draft, "Вариант изображения обновлён.")
        except Exception as exc:
            send_text(user_id, f"Не удалось обновить изображение: {str(exc)[:200]}")
        return
    if text.casefold().startswith("переделай:"):
        draft = pending_draft(user_id)
        if draft:
            try:
                draft["text"] = generate_post(user_id, f"Переделай этот черновик по пожеланиям пользователя.\nЧерновик: {draft['text']}\nПожелания: {text.split(':', 1)[1].strip()}")
                draft["status"] = "pending"
                save_state()
                show_draft(user_id, draft, "Текст черновика обновлён.")
            except Exception as exc:
                send_text(user_id, f"Не удалось изменить черновик: {str(exc)[:200]}")
        else:
            send_text(user_id, "Ожидающего черновика нет.")
        return
    if command == "/post":
        text = command_arg.strip()
        if not text and not incoming_image:
            send_text(user_id, "После /post добавьте тему, например: /post новость о новой услуге.")
            return
    if incoming_image:
        image_token = incoming_image
    else:
        image_token = state.get("uploads", {}).pop(user_id, None)
    state.get("uploads", {}).pop(user_id, None)
    try:
        create_draft(user_id, text, image_token=image_token)
    except Exception as exc:
        print(f"Task failed: {exc}")
        send_text(user_id, f"Не удалось подготовить черновик. Проверьте MAX/OpenAI настройки.\n{str(exc)[:250]}")


def answer_callback(callback_id):
    return max_api("/answers", "POST", {"callback_id": callback_id}, {"message": {"text": "Решение обрабатывается."}})


def handle_callback(update):
    callback = update.get("callback", {})
    user_id = str(callback.get("user", {}).get("user_id", ""))
    payload = callback.get("payload", "")
    callback_id = callback.get("callback_id")
    if callback_id:
        try:
            answer_callback(callback_id)
        except Exception as exc:
            print(f"Callback acknowledgment failed: {exc}")
    menu_actions = {
        "create_post": ("✍️ Опишите тему или цель поста сообщением.", None),
        "profile": (profile_text(user_id), None),
        "analytics": (publication_summary(user_id), None),
        "schedule": ("Расписание черновиков\n/schedule 09:00 — ежедневно подготовить черновик\n/schedule off — выключить. Публикация всегда ждёт вашего одобрения.", None),
        "help": ("Напишите задачу обычным сообщением или нажмите «Создать пост».\n/chatgpt — подключить ChatGPT\n/company — изменить профиль\n/channel — подключить канал\n/schedule — расписание\n/analytics — история публикаций\n/status — статус черновика\n/reset — сбросить профиль", None),
    }
    if payload in menu_actions:
        send_text(user_id, menu_actions[payload][0])
        return
    if payload == "reset_account":
        send_text(user_id, "Сбросить профиль, привязанный канал, черновики и подключение ChatGPT? Опубликованные посты останутся в канале.", [[callback_button("Подтвердить сброс", "reset_confirm"), callback_button("Отмена", "reset_cancel")]])
        return
    if payload == "reset_cancel":
        send_text(user_id, "Сброс отменён.")
        return
    if payload == "reset_confirm":
        reset_account(user_id)
        return
    if not state.get("companies", {}).get(user_id):
        send_text(user_id, "Сначала отправьте /start и завершите регистрацию.")
        return
    if callback.get("payload") == "connect_chatgpt":
        start_chatgpt_login(user_id)
        return
    if callback.get("payload") == "connect_channel":
        send_text(user_id, "Добавьте этого бота администратором MAX-канала с правом публикации. ID определится автоматически после добавления.")
        return
    action, sep, draft_id = payload.partition(":")
    draft = state["drafts"].get(draft_id) if sep else None
    if not draft or draft.get("owner_id") != user_id or draft.get("status") not in {"pending", "needs_review"}:
        send_text(user_id, "Черновик уже обработан или кнопка устарела.")
        return
    if action == "reject":
        draft["status"] = "rejected"
        save_state()
        send_text(user_id, "Черновик отклонён. Публикации не будет.")
    elif action in {"webimage", "image", "noimage"}:
        try:
            if action == "webimage":
                token, source = get_web_image(user_id, draft["text"])
                if not token:
                    send_text(user_id, "Не нашёл подходящее фото с разрешённой лицензией. Попробуйте /image описание.")
                    return
                draft["image_token"], draft["source_url"] = token, source
            elif action == "image":
                if not images_configured():
                    send_text(user_id, "Для генерации изображения владелец бота должен добавить OPENAI_API_KEY в .env. Поиск фото и публикация текста доступны без него.")
                    return
                draft["image_token"], draft["source_url"] = get_generated_image(user_id, draft["text"])
            else:
                draft.pop("image_token", None)
                draft.pop("source_url", None)
            save_state()
            show_draft(user_id, draft, "Вариант изображения обновлён." if action != "noimage" else "Изображение удалено.")
        except Exception as exc:
            send_text(user_id, f"Не получилось обновить изображение: {str(exc)[:200]}")
    elif action == "approve":
        # Allow a retry from needs_review only after the user has checked the channel.
        # Persist publishing first so duplicate callbacks cannot publish twice.
        draft["status"] = "publishing"
        save_state()
        try:
            result = send_channel(user_id, draft["text"], draft.get("image_token"), draft.get("source_url"))
        except Exception as exc:
            draft["status"] = "needs_review"
            save_state()
            send_text(user_id, f"MAX не подтвердил публикацию. Проверьте канал вручную перед повтором. Ошибка: {str(exc)[:180]}")
            return
        draft["status"] = "published"
        draft["published_at"] = int(time.time())
        draft["post_id"] = result.get("message", {}).get("mid") or result.get("mid")
        save_state()
        send_text(user_id, "✅ Пост опубликован в MAX-канале.")


def reset_account(user_id: str):
    state.get("companies", {}).pop(user_id, None)
    state.get("channels", {}).pop(user_id, None)
    state.get("pending_channels", {}).pop(user_id, None)
    state.get("registration", {}).pop(user_id, None)
    state.get("schedules", {}).pop(user_id, None)
    state.get("schedule_runs", {}).pop(user_id, None)
    state.get("uploads", {}).pop(user_id, None)
    for draft in state.get("drafts", {}).values():
        if draft.get("owner_id") == user_id and draft.get("status") in {"pending", "needs_review"}:
            draft["status"] = "superseded"
    auth_dir = ROOT / ".auth" / "max" / user_id
    shutil.rmtree(auth_dir, ignore_errors=True)
    save_state()
    send_text(user_id, "Профиль, подключение ChatGPT, канал и ожидающие черновики сброшены. Опубликованные записи остались в канале. Отправьте /start для настройки заново.")


def main():
    if not TOKEN:
        raise SystemExit("Добавьте MAX_BOT_TOKEN в .env")
    print("Запущен MAX polling-режим для разработки. Для production используйте Webhook.")
    threading.Thread(target=scheduled_job, daemon=True).start()
    params = {"timeout": 30, "limit": 100, "types": "bot_started,bot_added,message_created,message_callback"}
    while True:
        try:
            # The marker advances after each response. Refresh it before every
            # poll, otherwise MAX keeps returning the same updates and the bot
            # sends the same replies forever.
            if state.get("marker") is not None:
                params["marker"] = state["marker"]
            else:
                params.pop("marker", None)
            result = max_api("/updates", params=params)
            for update in result.get("updates", []):
                if update.get("update_type") == "bot_added":
                    handle_bot_added(update)
                elif update.get("update_type") == "bot_started":
                    user_id = str(update.get("user", {}).get("user_id", ""))
                    if user_id:
                        if not state.get("companies", {}).get(user_id) and user_id not in state.get("registration", {}):
                            begin_registration(user_id)
                        elif state.get("companies", {}).get(user_id):
                            greet(user_id)
                elif update.get("update_type") == "message_created":
                    handle_message(update)
                elif update.get("update_type") == "message_callback":
                    handle_callback(update)
            if result.get("marker") is not None:
                state["marker"] = result["marker"]
                save_state()
        except KeyboardInterrupt:
            print("MAX-бот остановлен")
            break
        except Exception as exc:
            print(f"MAX polling error: {exc}")
            time.sleep(3)


if __name__ == "__main__":
    main()
