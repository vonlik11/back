"""VK adapter for the AI marketer: draft first, publish only after approval."""
from __future__ import annotations

import json
import os
import queue
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
STATE_PATH = ROOT / "vk_state.json"
API_ROOT = "https://api.vk.ru/method/"


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
TOKEN = os.getenv("VK_GROUP_TOKEN", "")
GROUP_ID = os.getenv("VK_GROUP_ID", "")
ADMIN_ID = os.getenv("VK_ADMIN_USER_ID", "")
API_VERSION = os.getenv("VK_API_VERSION", "5.199")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Yekaterinburg")
DAILY_AI_LIMIT = max(1, int(os.getenv("DAILY_AI_LIMIT", "30")))
DAILY_IMAGE_LIMIT = max(1, int(os.getenv("DAILY_IMAGE_LIMIT", "10")))
try:
    LOCAL_TZ = ZoneInfo(TIMEZONE)
except ZoneInfoNotFoundError:
    TIMEZONE = "UTC"
    try:
        LOCAL_TZ = ZoneInfo(TIMEZONE)
    except ZoneInfoNotFoundError:
        # Windows may have no IANA database until tzdata is installed.
        LOCAL_TZ = timezone.utc


def node_command() -> str:
    configured = os.getenv("NODE_EXECUTABLE")
    candidate = configured or shutil.which("node") or shutil.which("node.exe")
    if candidate:
        return candidate
    if os.name == "nt":
        for candidate in (r"C:\Program Files\nodejs\node.exe", os.path.expandvars(r"%LOCALAPPDATA%\Programs\nodejs\node.exe")):
            if candidate and Path(candidate).is_file():
                return candidate
    raise RuntimeError("Не найден Node.js. Установите Node.js 22.12+ и перезапустите терминал.")


def read_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


state = read_state()
state.setdefault("company", {})
state.setdefault("registration", {})
state.setdefault("drafts", {})
state.setdefault("schedule", None)
state.setdefault("schedule_runs", {})
state.setdefault("ai_usage", {})
state.setdefault("image_usage", {})
auth_logins = {}
AUTH_LOGIN_LOCK = threading.Lock()
API_PROVIDERS = {
    "openai": ("OpenAI API", "https://platform.openai.com/api-keys"),
    "gemini": ("Google Gemini API", "https://aistudio.google.com/app/apikey"),
    "claude": ("Anthropic Claude API", "https://console.anthropic.com/settings/keys"),
    "deepseek": ("DeepSeek API", "https://platform.deepseek.com/api_keys"),
}
if state.get("draft") and not state["drafts"]:
    legacy = dict(state["draft"])
    legacy.setdefault("id", uuid.uuid4().hex[:12])
    state["drafts"][legacy["id"]] = legacy
state.pop("draft", None)


def save_state() -> None:
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_PATH)


def vk_call(method: str, params: dict):
    values = {**params, "access_token": TOKEN, "v": API_VERSION}
    # This installation previously verified TLS/API connectivity with VK's GET
    # endpoint. Keep VK method parameters in the query string for compatibility.
    url = API_ROOT + method + "?" + urllib.parse.urlencode(values)
    request = urllib.request.Request(url)
    try:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", None)
                retryable_tls = isinstance(reason, (TimeoutError, OSError)) and (
                    "SSL" in str(reason) or "EOF" in str(reason) or isinstance(reason, TimeoutError)
                )
                if attempt == 2 or not retryable_tls:
                    raise
                time.sleep(1 + attempt)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"VK API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Не удалось соединиться с VK API: {exc}") from exc
    if "error" in data:
        error = data["error"]
        raise RuntimeError(f"VK API {error.get('error_code')}: {error.get('error_msg', 'ошибка API')}")
    return data.get("response", {})


def main_keyboard() -> str:
    labels = [
        ("✍️ Создать пост", "primary"), ("👤 Профиль", "secondary"),
        ("📊 Публикации", "secondary"), ("⏰ Расписание", "secondary"),
        ("🔑 AI API", "secondary"),
        ("❓ Помощь", "secondary"),
    ]
    buttons = []
    for label, color in labels:
        buttons.append([{"action": {"type": "text", "label": label}, "color": color}])
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def send(peer_id: int, text: str, *, keyboard: str | None = None, attachment: str | None = None):
    params = {"peer_id": peer_id, "random_id": int(time.time_ns() % 2_147_483_647), "message": text[:4000]}
    if keyboard:
        params["keyboard"] = keyboard
    if attachment:
        params["attachment"] = attachment
    return vk_call("messages.send", params)


def send_menu(peer_id: int, text: str):
    return send(peer_id, text, keyboard=main_keyboard())


def admin_user_id() -> str:
    return ADMIN_ID


def auth_path(user_id: str) -> Path:
    return ROOT / ".auth" / "vk" / user_id / "chatgpt.enc"


def chatgpt_connected(user_id: str) -> bool:
    return auth_path(user_id).is_file() or (auth_path(user_id).parent / "api-key.enc").is_file()


def ai_provider(user_id: str) -> str:
    path = auth_path(user_id).parent
    try:
        provider = (path / "provider").read_text(encoding="utf-8").strip()
        return provider if provider in {"chatgpt", "api"} else "chatgpt"
    except OSError:
        return "api" if (path / "api-key.enc").is_file() else "chatgpt"


def local_date() -> str:
    return datetime.now(LOCAL_TZ).date().isoformat()


def reserve_usage(field: str, limit: int) -> None:
    day = state.setdefault(field, {})
    today = local_date()
    if day.get("date") != today:
        day.clear()
        day.update(date=today, total=0)
    if int(day.get("total", 0)) >= limit:
        raise RuntimeError("Дневной лимит исчерпан. Попробуйте завтра.")
    day["total"] = int(day.get("total", 0)) + 1
    save_state()


def ai_text(user_id: str, prompt: str, instructions: str, *, reserve=True) -> str:
    if reserve:
        reserve_usage("ai_usage", DAILY_AI_LIMIT)
    provider = ai_provider(user_id)
    result = subprocess.run(
        [node_command(), "siwc.mjs", "infer-api-vk" if provider == "api" else "infer-vk", user_id], cwd=ROOT,
        input=json.dumps({"prompt": prompt, "instructions": instructions}, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", timeout=150,
    )
    if result.returncode:
        messages = {
            "chatgpt_not_connected": "VK-бот не нашёл вход ChatGPT для вашего VK ID. Отправьте /chatgpt и войдите в нужный аккаунт.",
            "chatgpt_session_expired": "Сессия ChatGPT устарела. Отправьте /chatgpt, завершите вход в браузере и повторите задачу.",
            "chatgpt_access_denied": "ChatGPT не разрешил запрос. Повторите вход командой /chatgpt и подтвердите доступ.",
            "chatgpt_model_unavailable": "Выбранная модель недоступна этой сессии. Проверьте CHATGPT_MODEL в .env.",
            "openai_network_error": "Не удалось соединиться с OpenAI. Проверьте интернет и повторите позже.",
            "api_http_401": "API-провайдер отклонил ключ. Подключите новый через /api.",
            "api_http_402": "Недостаточно средств на балансе API-провайдера.",
            "api_http_403": "У API-ключа нет доступа к модели или запросу.",
            "api_http_404": "Модель не найдена. Проверьте имя модели в настройках .env.",
            "api_http_429": "Превышен лимит API-провайдера. Попробуйте позже или проверьте оплату.",
        }
        code = result.stderr.strip()
        default = "API-провайдер не ответил. Проверьте ключ и повторите /api." if provider == "api" else "ChatGPT не ответил. Повторите /chatgpt; если ошибка останется, пришлите полный текст ошибки."
        raise RuntimeError(messages.get(code, default))
    answer = json.loads(result.stdout).get("text", "").strip()
    if not answer:
        raise RuntimeError("Модель вернула пустой ответ.")
    return answer


def generate_post(user_id: str, prompt: str) -> str:
    company = state.get("company", {})
    instructions = (
        "Ты AI-маркетолог сообщества ВКонтакте. Напиши ясный, живой пост на русском языке. "
        "Опирайся на профиль компании и сведения из запроса; не выдумывай факты, цены, результаты, даты и источники. "
        "Если пользователь требует только проверенные сведения, не называй сведения проверенными без реальных источников; "
        "если источников нет, попроси пользователя прислать их или подготовь нейтральный текст без спорных фактов. "
        "Верни только текст поста, без пояснений.\n"
        f"Профиль компании: {json.dumps(company, ensure_ascii=False)}\nЗадача: {prompt}"
    )
    return ai_text(user_id, prompt, instructions)[:3500]


def multipart_upload(url: str, image: bytes, field: str) -> dict:
    boundary = "----CodexVK" + uuid.uuid4().hex
    extension, mime = ("png", "image/png") if image.startswith(b"\x89PNG\r\n\x1a\n") else ("jpg", "image/jpeg")
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"post.{extension}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("ascii") + image + f"\r\n--{boundary}--\r\n".encode("ascii")
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_wall_photo(image: bytes) -> str:
    server = vk_call("photos.getWallUploadServer", {"group_id": int(GROUP_ID)})
    uploaded = multipart_upload(server["upload_url"], image, "photo")
    saved = vk_call("photos.saveWallPhoto", {
        "group_id": int(GROUP_ID), "photo": uploaded["photo"],
        "server": uploaded["server"], "hash": uploaded["hash"],
    })
    photo = saved[0]
    return f"photo{photo['owner_id']}_{photo['id']}"


def get_web_image(user_id: str, prompt: str):
    query = ai_text(user_id, prompt, "Составь поисковую фразу для тематического фото, 2–6 слов. Верни только фразу.")
    candidates = search_images(query)
    if not candidates:
        return None, None
    selected = candidates[0]
    return upload_wall_photo(download_image(selected["download_url"])), selected["url"]


def get_generated_image(user_id: str, prompt: str):
    if not images_configured():
        raise RuntimeError("Генерация изображений не настроена: добавьте OPENAI_API_KEY в .env.")
    reserve_usage("image_usage", DAILY_IMAGE_LIMIT)
    image = generate_image(prompt, user_id)
    return upload_wall_photo(image), None


def draft_keyboard() -> str:
    buttons = [
        [{"action": {"type": "text", "label": "✅ Одобрить и опубликовать"}, "color": "positive"}],
        [{"action": {"type": "text", "label": "🗑 Отклонить"}, "color": "negative"}],
        [{"action": {"type": "text", "label": "🔎 Найти фото"}, "color": "secondary"},
         {"action": {"type": "text", "label": "🎨 Создать картинку"}, "color": "secondary"}],
    ]
    return json.dumps({"one_time": False, "buttons": buttons}, ensure_ascii=False)


def pending_draft():
    return next((draft for draft in reversed(list(state["drafts"].values()))
                 if draft.get("status") in {"pending", "needs_review"}), None)


def show_draft(peer_id: int, draft: dict, note=""):
    source = f"\n\nИсточник изображения: {draft['source_url']}" if draft.get("source_url") else ""
    intro = f"{note}\n\n" if note else ""
    send(peer_id,
         f"{intro}📝 Черновик поста\n\n{draft['text']}{source}\n\nПроверьте текст. Нажмите кнопку ниже или отправьте /approve, /reject, /edit текст, «переделай: пожелание». Про публикацию без одобрения речи нет.",
         keyboard=draft_keyboard(), attachment=draft.get("attachment"))


def create_draft(peer_id: int, user_id: str, prompt: str, attachment=None, source_url=None):
    send(peer_id, "Готовлю черновик…")
    text = generate_post(user_id, prompt)
    if not attachment and not re.search(r"\b(только текст|без картинки|без фото)\b", prompt, re.IGNORECASE):
        try:
            attachment, source_url = get_web_image(user_id, prompt)
        except Exception as exc:
            print(f"Image search skipped: {exc}")
    for item in state["drafts"].values():
        if item.get("status") == "pending":
            item["status"] = "superseded"
    draft = {"id": uuid.uuid4().hex[:12], "owner_id": user_id, "text": text,
             "status": "pending", "created_at": int(time.time())}
    if attachment:
        draft["attachment"] = attachment
    if source_url:
        draft["source_url"] = source_url
    state["drafts"][draft["id"]] = draft
    save_state()
    show_draft(peer_id, draft)


def publish(peer_id: int):
    draft = pending_draft()
    if not draft:
        send(peer_id, "Нет черновика, ожидающего решения.")
        return
    draft["status"] = "publishing"
    draft.setdefault("guid", uuid.uuid4().hex)
    save_state()
    post_text = draft["text"]
    if draft.get("source_url"):
        post_text = f"{post_text}\n\nИсточник изображения: {draft['source_url']}"
    params = {"owner_id": -int(GROUP_ID), "from_group": 1, "message": post_text, "guid": draft["guid"]}
    if draft.get("attachment"):
        params["attachments"] = draft["attachment"]
    try:
        response = vk_call("wall.post", params)
        draft["status"] = "published"
        draft["post_id"] = response.get("post_id")
        draft["published_at"] = int(time.time())
        save_state()
        send(peer_id, f"✅ Пост опубликован в сообществе. ID записи: {draft['post_id']}")
    except Exception as exc:
        draft["status"] = "needs_review"
        save_state()
        send(peer_id, f"VK не подтвердил публикацию. Сначала проверьте стену вручную и только затем повторяйте. Ошибка: {str(exc)[:220]}")


def edit_draft(peer_id: int, user_id: str, instruction: str):
    draft = pending_draft()
    if not draft:
        send(peer_id, "Нет черновика, который можно изменить.")
        return
    text = generate_post(user_id, f"Переделай этот пост с учётом пожеланий. Не добавляй новых фактов.\nЧерновик: {draft['text']}\nПожелания: {instruction}")
    draft["text"] = text
    draft["status"] = "pending"
    save_state()
    show_draft(peer_id, draft, "Текст черновика обновлён.")


def start_chatgpt_login(peer_id: int, user_id: str):
    start_login_process(peer_id, user_id, "chatgpt")


def start_api_login(peer_id: int, user_id: str, provider: str | None = None):
    if provider not in API_PROVIDERS:
        buttons = []
        for key, (name, _) in API_PROVIDERS.items():
            buttons.append([{"action": {"type": "text", "label": f"/api {key}"}, "color": "secondary"}])
        keyboard = json.dumps({"one_time": True, "buttons": buttons}, ensure_ascii=False)
        send(peer_id, "Выберите API-провайдера. Ключ вводится в локальной форме на компьютере с ботом.", keyboard=keyboard)
        return
    start_login_process(peer_id, user_id, "api", provider)


def start_login_process(peer_id: int, user_id: str, kind: str, provider: str | None = None):
    with AUTH_LOGIN_LOCK:
        if user_id in auth_logins:
            send(peer_id, "Для аккаунта уже начат вход. Завершите его и повторите команду.")
            return
        command = "api-login-vk" if kind == "api" else "login-vk"
        args = [node_command(), "siwc.mjs", command, user_id]
        if provider:
            args.append(provider)
        try:
            proc = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, encoding="utf-8")
        except (OSError, RuntimeError) as exc:
            send(peer_id, f"Не удалось запустить вход: {str(exc)[:220]}")
            return
        auth_logins[user_id] = proc
    try:
        info = json.loads(proc.stdout.readline())
        if kind == "api":
            name = API_PROVIDERS[provider][0]
            send(peer_id, f"Откройте локальную страницу на компьютере с ботом и введите там ключ {name}. Ключ не отправляйте в VK. Ссылка действует 10 минут:\n{info['url']}")
        else:
            send(peer_id, f"Откройте ссылку, войдите в ChatGPT и разрешите подключение:\n{info['url']}\n\nСсылка действует около 10 минут.")
    except Exception as exc:
        proc.terminate()
        with AUTH_LOGIN_LOCK:
            auth_logins.pop(user_id, None)
        send(peer_id, f"Не удалось начать вход. Проверьте Node.js и npm install: {str(exc)[:180]}")
        return

    def finish():
        try:
            code = proc.wait(timeout=660)
            name = API_PROVIDERS[provider][0] if provider else "ChatGPT"
            message = f"✅ {name} подключён. Выберите действие или напишите тему поста." if code == 0 else "Вход не завершился. Если OpenAI сообщает о неподдерживаемом регионе, это ограничение сервиса; попробуйте API через /api."
            send_menu(peer_id, message)
        except subprocess.TimeoutExpired:
            proc.terminate()
            send(peer_id, "Ссылка истекла. Для нового входа повторите команду подключения.")
        finally:
            if proc.stdout:
                proc.stdout.close()
            with AUTH_LOGIN_LOCK:
                if auth_logins.get(user_id) is proc:
                    auth_logins.pop(user_id, None)
    threading.Thread(target=finish, daemon=True).start()


def handle_profile(peer_id: int, text: str):
    form = state.get("registration", {})
    if not form:
        state["registration"] = {"step": "name"}
        save_state()
        send(peer_id, "Напишите название компании.")
        return True
    if form["step"] == "name":
        if not text or len(text) > 120:
            send(peer_id, "Название должно содержать от 1 до 120 символов.")
            return True
        form.update(name=text, step="description")
        save_state()
        send(peer_id, "Опишите компанию, её аудиторию и желаемый стиль публикаций.")
        return True
    state["company"] = {"name": form.get("name", ""), "description": text[:2000]}
    state["registration"] = {}
    save_state()
    send_menu(peer_id, "✅ Профиль компании сохранён. Теперь подключите ChatGPT командой /chatgpt или выберите действие.")
    return True


def schedule_command(peer_id: int, text: str):
    parts = text.split(maxsplit=1)
    current = state.get("schedule")
    if len(parts) == 1:
        send(peer_id, f"Расписание: {current or 'выключено'} ({TIMEZONE}).\n/schedule 09:00 — ежедневный черновик\n/schedule off — выключить. Публикация только после одобрения.")
        return
    value = parts[1].strip().lower()
    if value == "off":
        state["schedule"] = None
        state["schedule_runs"] = {}
        save_state()
        send(peer_id, "Ежедневная подготовка черновика выключена.")
    elif re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        state["schedule"] = value
        save_state()
        send(peer_id, f"Готово. Каждый день в {value} ({TIMEZONE}) будет подготовлен черновик. Автоматической публикации не будет.")
    else:
        send(peer_id, "Укажите время как ЧЧ:ММ, например /schedule 09:00, или /schedule off.")


def scheduled_job():
    while True:
        now = datetime.now(LOCAL_TZ)
        today = now.date().isoformat()
        if state.get("schedule") == now.strftime("%H:%M") and state.get("schedule_runs", {}).get(today) != today:
            state["schedule_runs"][today] = today
            save_state()
            if state.get("company") and ADMIN_ID and chatgpt_connected(ADMIN_ID):
                try:
                    create_draft(int(ADMIN_ID), ADMIN_ID,
                                 f"Подготовь ежедневный полезный пост для компании {state['company'].get('name', '')}. Используй только сведения из профиля.")
                except Exception as exc:
                    print(f"Scheduled draft failed: {exc}")
        time.sleep(20)


def handle_message(message: dict):
    user_id = str(message.get("from_id", ""))
    peer_id = int(message.get("peer_id", user_id or 0))
    if not user_id or peer_id != int(user_id):
        return
    text = (message.get("text") or "").strip()
    if re.search(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,})", text):
        send(peer_id, "API-ключ уже попал в историю VK. Удалите это сообщение вручную. Подключайте ключ через /api и вводите его только в локальной форме на компьютере с ботом.")
        return
    if text == "/id":
        send(peer_id, f"Ваш VK user ID: {user_id}. Добавьте его в VK_ADMIN_USER_ID в .env.")
        return
    if not ADMIN_ID or user_id != ADMIN_ID:
        send(peer_id, "У бота закрытый режим. Доступ есть только у VK_ADMIN_USER_ID из .env.")
        return
    command, _, argument = text.partition(" ")
    command = command.lower()
    menu = {"✍️ Создать пост": "/post", "📊 Публикации": "/analytics", "⏰ Расписание": "/schedule", "🔑 AI API": "/api", "❓ Помощь": "/help"}
    text = menu.get(text, text)
    command, _, argument = text.partition(" ")
    command = command.lower()
    if command == "/chatgpt":
        start_chatgpt_login(peer_id, user_id)
        return
    if command == "/api":
        provider = argument.strip().casefold() if argument.strip() else None
        start_api_login(peer_id, user_id, provider)
        return
    if command in {"/start", "/help"}:
        if not state.get("company"):
            state["registration"] = {"step": "name"}
            save_state()
            send(peer_id, "Здравствуйте! Я AI-маркетолог вашего сообщества. Напишите название компании.")
        else:
            send_menu(peer_id, "Главное меню\n\nНапишите задачу обычным сообщением или нажмите «Создать пост». Черновик появится здесь; публикация — только после вашего одобрения.\n\nКоманды: /profile, /chatgpt, /api, /schedule, /analytics, /status, /reset.")
        return
    if state.get("registration") and command not in {"/cancel", "/help"}:
        handle_profile(peer_id, text)
        return
    if command == "/company":
        state["registration"] = {"step": "name"}
        save_state()
        send(peer_id, "Изменяем профиль. Напишите название компании.")
        return
    if command == "/profile":
        if state.get("company"):
            company = state["company"]
            send(peer_id, f"Компания: {company.get('name', '—')}\nОписание: {company.get('description', '—')}\nAI: {'подключён' if chatgpt_connected(user_id) else 'не подключён'}\n\nЧтобы изменить профиль, отправьте /company.")
        else:
            state["registration"] = {"step": "name"}
            save_state()
            send(peer_id, "Напишите название компании.")
        return
    if command == "/cancel":
        state["registration"] = {}
        save_state()
        send_menu(peer_id, "Настройка отменена.")
        return
    if command == "/schedule":
        schedule_command(peer_id, text)
        return
    if command == "/analytics":
        posts = [d for d in state["drafts"].values() if d.get("status") == "published"]
        posts.sort(key=lambda d: d.get("published_at", 0), reverse=True)
        lines = [f"Опубликовано ботом: {len(posts)} постов."]
        for draft in posts[:10]:
            when = datetime.fromtimestamp(draft.get("published_at", 0), LOCAL_TZ).strftime("%d.%m %H:%M")
            lines.append(f"• {when} — {draft.get('text', '').replace(chr(10), ' ')[:90]}")
        send(peer_id, "\n".join(lines) if posts else "Публикаций пока нет.")
        return
    if command == "/status":
        draft = pending_draft()
        send(peer_id, f"Статус черновика: {draft.get('status')}\n\n{draft.get('text', '')}" if draft else "Ожидающего черновика нет.")
        return
    if command == "/reset":
        if argument.strip().lower() != "confirm":
            send(peer_id, "Чтобы сбросить профиль, авторизацию и черновики, отправьте /reset confirm. Опубликованные посты останутся в сообществе.")
            return
        for draft in state["drafts"].values():
            if draft.get("status") in {"pending", "needs_review"}:
                draft["status"] = "superseded"
        state.update(company={}, registration={}, schedule=None, schedule_runs={})
        save_state()
        shutil.rmtree(ROOT / ".auth" / "vk" / user_id, ignore_errors=True)
        send(peer_id, "Профиль и подключение AI сброшены. Опубликованные посты не затронуты.")
        return
    if text == "✅ Одобрить и опубликовать" or command == "/approve":
        publish(peer_id)
        return
    if text == "🗑 Отклонить" or command == "/reject":
        draft = pending_draft()
        if draft:
            draft["status"] = "rejected"
            save_state()
            send(peer_id, "Черновик отклонён. Публикации не будет.")
        else:
            send(peer_id, "Нет ожидающего черновика.")
        return
    if text.startswith("переделай:"):
        edit_draft(peer_id, user_id, text.split(":", 1)[1].strip())
        return
    if command == "/edit":
        edit_draft(peer_id, user_id, argument.strip())
        return
    draft = pending_draft()
    if text == "🔎 Найти фото" or command == "/findimage":
        if not draft:
            send(peer_id, "Сначала подготовьте черновик.")
            return
        query = argument.strip() or draft["text"]
        try:
            attachment, source_url = get_web_image(user_id, query)
            if not attachment:
                send(peer_id, "Не нашёл подходящую иллюстрацию. Попробуйте позже или отправьте своё фото с подписью /post.")
                return
            draft["attachment"], draft["source_url"] = attachment, source_url
            save_state()
            show_draft(peer_id, draft, "Изображение добавлено.")
        except Exception as exc:
            send(peer_id, f"Не удалось найти изображение: {str(exc)[:200]}")
        return
    if text == "🎨 Создать картинку" or command == "/image":
        if not draft:
            send(peer_id, "Сначала подготовьте черновик.")
            return
        try:
            draft["attachment"], draft["source_url"] = get_generated_image(user_id, argument.strip() or draft["text"])
            save_state()
            show_draft(peer_id, draft, "Изображение создано.")
        except Exception as exc:
            send(peer_id, f"Не удалось создать изображение: {str(exc)[:200]}")
        return
    if command == "/noimage":
        if draft:
            draft.pop("attachment", None)
            draft.pop("source_url", None)
            save_state()
            show_draft(peer_id, draft, "Изображение удалено.")
        return
    if command == "/post":
        if argument.strip():
            text = argument.strip()
        else:
            send(peer_id, "Напишите тему поста следующим сообщением.")
            state["awaiting_task"] = True
            save_state()
            return
    elif text in {"✍️ Создать пост"}:
        send(peer_id, "Напишите тему поста следующим сообщением.")
        state["awaiting_task"] = True
        save_state()
        return
    elif text in {"👤 Профиль"}:
        company = state.get("company", {})
        send(peer_id, f"Компания: {company.get('name', '—')}\nОписание: {company.get('description', '—')}\nAI: {'подключён' if chatgpt_connected(user_id) else 'не подключён'}\n\n/profile — изменить профиль")
        return
    elif text in {"📊 Публикации"}:
        # Route through the regular command handler on the next message.
        text = "/analytics"
        return handle_message({**message, "text": text})
    elif text in {"⏰ Расписание"}:
        schedule_command(peer_id, "/schedule")
        return
    if state.pop("awaiting_task", False) or (text and not text.startswith("/")):
        if not state.get("company"):
            state["registration"] = {"step": "name"}
            save_state()
            send(peer_id, "Сначала настроим компанию. Напишите её название.")
            return
        if not chatgpt_connected(user_id):
            send(peer_id, "Сначала подключите ChatGPT командой /chatgpt.")
            return
        incoming_photo = None
        for attachment in message.get("attachments", []):
            photo = attachment.get("photo") if isinstance(attachment, dict) else None
            sizes = photo.get("sizes", []) if isinstance(photo, dict) else []
            if sizes:
                incoming_photo = max(sizes, key=lambda size: size.get("width", 0) * size.get("height", 0)).get("url")
                break
        upload = None
        if incoming_photo:
            try:
                parts = urllib.parse.urlsplit(incoming_photo)
                host = (parts.hostname or "").lower()
                allowed = host.endswith(".userapi.com") or host.endswith(".vkuserphoto.ru") or host.endswith(".vk.com")
                if parts.scheme != "https" or not allowed:
                    raise RuntimeError("Неподдерживаемый адрес изображения")
                with urllib.request.urlopen(incoming_photo, timeout=40) as response:
                    image = response.read(10 * 1024 * 1024 + 1)
                if len(image) > 10 * 1024 * 1024 or not (image.startswith(b"\xff\xd8\xff") or image.startswith(b"\x89PNG\r\n\x1a\n")):
                    raise RuntimeError("Нужно изображение JPEG или PNG до 10 МБ")
                upload = upload_wall_photo(image)
            except Exception as exc:
                send(peer_id, f"Не удалось использовать ваше фото: {str(exc)[:160]}")
        try:
            create_draft(peer_id, user_id, text or "Подготовь пост на основе фотографии.", attachment=upload)
        except Exception as exc:
            send(peer_id, f"Не удалось подготовить черновик: {str(exc)[:220]}")
        return
    if text:
        send_menu(peer_id, "Не распознал действие. Напишите тему поста или выберите пункт в меню.")


def get_longpoll():
    data = vk_call("groups.getLongPollServer", {"group_id": int(GROUP_ID)})
    return data["server"], data["key"], str(data["ts"])


message_queue: queue.Queue[dict] = queue.Queue()


def message_worker():
    """Process updates serially without pausing the Long Poll connection."""
    while True:
        message = message_queue.get()
        try:
            handle_message(message)
        except Exception as exc:
            print(f"Update failed: {exc}")
        finally:
            message_queue.task_done()


def main():
    if not TOKEN or not GROUP_ID:
        raise SystemExit("Заполните VK_GROUP_TOKEN и VK_GROUP_ID в .env")
    if not ADMIN_ID:
        print("Отправьте сообществу /id, добавьте VK_ADMIN_USER_ID в .env и перезапустите бота.")
    threading.Thread(target=message_worker, daemon=True).start()
    threading.Thread(target=scheduled_job, daemon=True).start()
    print(f"VK-бот запущен. Часовой пояс: {TIMEZONE}. Для личных сообщений включите сообщения сообщества и Bots Long Poll.")
    server = key = ts = None
    consecutive_errors = 0
    while True:
        try:
            if server is None:
                server, key, ts = get_longpoll()
            params = urllib.parse.urlencode({"act": "a_check", "key": key, "ts": ts, "wait": 25})
            with urllib.request.urlopen(f"{server}?{params}", timeout=90) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("failed"):
                if result["failed"] == 1:
                    ts = str(result.get("ts", ts))
                else:
                    server, key, ts = get_longpoll()
                continue
            ts = str(result.get("ts", ts))
            consecutive_errors = 0
            for update in result.get("updates", []):
                if update.get("type") == "message_new":
                    message_queue.put(update.get("object", {}).get("message", {}))
        except KeyboardInterrupt:
            print("VK-бот остановлен")
            break
        except Exception as exc:
            print(f"VK Long Poll error: {exc}")
            consecutive_errors += 1
            time.sleep(min(30, 3 * (2 ** min(consecutive_errors - 1, 4))) + (consecutive_errors % 3))
            server = key = ts = None


if __name__ == "__main__":
    main()
