from __future__ import annotations

import json
import hashlib
import hmac
from functools import wraps
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from image_provider import ImageError, configured as images_configured, generate_image
from web_images import WebImageError, search_images, download_image
from user_database import UserDatabase

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
PROFILE_FILE = ROOT / "company.json"
DATABASE_FILE = ROOT / "users.sqlite3"
TOKEN_FROM_ENVIRONMENT = "TELEGRAM_BOT_TOKEN" in os.environ


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
CHATGPT_MODEL = os.getenv("CHATGPT_MODEL", "gpt-6.1-sol")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Yekaterinburg")
SCHEDULE_TIME = os.getenv("SCHEDULE_TIME", "09:00")
DAILY_AI_LIMIT = int(os.getenv("DAILY_AI_LIMIT", "30"))
DAILY_USER_LIMIT = int(os.getenv("DAILY_USER_LIMIT", "5"))
DAILY_IMAGE_LIMIT = int(os.getenv("DAILY_IMAGE_LIMIT", "10"))
DAILY_USER_IMAGE_LIMIT = int(os.getenv("DAILY_USER_IMAGE_LIMIT", "3"))
BOT_ID = None
chatgpt_logins = {}
chatgpt_logins_lock = threading.Lock()
api_key_requests = set()
reset_requests = {}
publish_jobs = {}
bot_state_lock = threading.RLock()
MINIAPP_URL = os.getenv("MINIAPP_URL", "").strip()
MINIAPP_PORT = os.getenv("MINIAPP_PORT", "3000").strip()
BOT_USERNAME = ""


def with_bot_state(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with bot_state_lock:
            return function(*args, **kwargs)
    return locked


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    contents = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    keep_pending = False
    try:
        tmp.write_text(contents, encoding="utf-8")
        for attempt in range(10):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == 9:
                    keep_pending = path == STATE_FILE
                    raise
                time.sleep(min(0.1 * 2 ** attempt, 0.5))
    finally:
        if not keep_pending:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


state = read_json(STATE_FILE, {"offset": 0, "chats": {}, "drafts": {}, "schedule_date": ""})
workspaces = state.setdefault("workspaces", {})
user_database = UserDatabase(DATABASE_FILE)
user_database.migrate_accounts_json(ROOT / "accounts.json")


def recover_pending_state():
    try:
        saved_at = STATE_FILE.stat().st_mtime_ns
    except FileNotFoundError:
        return
    candidates = [STATE_FILE.with_suffix(".json.tmp")]
    candidates.extend(STATE_FILE.parent.glob(f"{STATE_FILE.name}.*.tmp"))
    pending_files = []
    for candidate in candidates:
        try:
            modified_at = candidate.stat().st_mtime_ns
            if modified_at > saved_at:
                pending_files.append((modified_at, candidate))
        except FileNotFoundError:
            continue
    for _, candidate in sorted(pending_files, reverse=True):
        pending = read_json(candidate, None)
        if not isinstance(pending, dict) or pending.get("offset") != state.get("offset"):
            continue
        if not all(isinstance(pending.get(key), dict) for key in ("chats", "drafts", "workspaces")):
            continue
        write_json(STATE_FILE, pending)
        workspaces.clear()
        workspaces.update(pending["workspaces"])
        state.clear()
        state.update(pending, workspaces=workspaces)
        return


def recover_abandoned_publications():
    changed = False
    for draft in state.get("drafts", {}).values():
        if draft.get("status") == "publishing":
            draft["status"] = "publication_unknown"
            draft["publish_error"] = "Бот перезапустился во время отправки. Проверьте канал: сообщение могло успеть появиться."
            changed = True
    if changed:
        write_json(STATE_FILE, state)


def retire_drafts(chat_id: str):
    for draft in state["drafts"].values():
        if draft["chat_id"] == chat_id and draft["status"] == "pending":
            draft["status"] = "superseded"


def usable_profile(profile) -> bool:
    return (
        isinstance(profile, dict)
        and bool(profile.get("description") or profile.get("name"))
        and str(profile.get("name", "")).strip().casefold() != "листва coffee"
    )


def migrate_profiles():
    for chat_id, workspace in list(workspaces.items()):
        if not usable_profile(workspace.get("profile")):
            del workspaces[chat_id]
            state["chats"].pop(chat_id, None)
            retire_drafts(chat_id)
    if ADMIN_CHAT_ID and CHANNEL_ID and ADMIN_CHAT_ID not in workspaces and not state.get("legacy_profile_reset"):
        profile = read_json(PROFILE_FILE, {})
        if usable_profile(profile):
            workspaces[ADMIN_CHAT_ID] = {
                "channel": CHANNEL_ID, "profile": profile, "schedule": SCHEDULE_TIME,
                "schedule_date": state.get("schedule_date", ""),
            }
            for draft in state["drafts"].values():
                if draft["chat_id"] == ADMIN_CHAT_ID:
                    draft.setdefault("channel", CHANNEL_ID)
        else:
            state["chats"].pop(ADMIN_CHAT_ID, None)
            retire_drafts(ADMIN_CHAT_ID)
    write_json(STATE_FILE, state)


class TelegramError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class SetupError(Exception):
    pass


def bot_time():
    try:
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        if TIMEZONE == "Asia/Yekaterinburg":
            return datetime.now(timezone(timedelta(hours=5)))
        return datetime.now().astimezone()


def http_json(url: str, payload: dict | None = None, headers: dict | None = None, timeout: int = 40):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise TelegramError(f"HTTP {exc.code}: {body[:500]}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise TelegramError(str(exc.reason)) from exc


def tg(method: str, payload: dict | None = None):
    if not BOT_TOKEN:
        raise TelegramError("В .env не задан TELEGRAM_BOT_TOKEN")
    result = http_json(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", payload, {"Content-Type": "application/json"}, timeout=35)
    if not result.get("ok"):
        raise TelegramError(result.get("description", "Ошибка Telegram Bot API"), result.get("error_code"))
    return result.get("result")


def conflict_message(error: TelegramError) -> str:
    detail = str(error).casefold()
    if "webhook" in detail:
        return "Telegram 409: у этого бота включён webhook. Отключите прежнюю webhook-интеграцию перед запуском этой версии."
    if "getupdates" in detail and "terminated" in detail:
        return "Telegram 409: другой процесс получает сообщения этого же бота. Оставьте один запуск; проверьте имя бота через python main.py --check на обоих компьютерах."
    return "Telegram 409: конфликт получения сообщений. Выполните python main.py --check для проверки бота и webhook."


def check_connection():
    global BOT_USERNAME
    print(f"Папка бота: {ROOT}")
    source = "окружение процесса (приоритет над .env)" if TOKEN_FROM_ENVIRONMENT else str(ROOT / ".env")
    print(f"Источник токена: {source}")
    if TOKEN_FROM_ENVIRONMENT:
        print("Если меняли только .env, старое значение в окружении могло остаться активным.")
    identity = tg("getMe")
    BOT_USERNAME = identity.get("username", "")
    print(f"Telegram-бот: @{identity.get('username', '(без username)')}; ID: {identity['id']}")
    webhook = tg("getWebhookInfo")
    active = bool(webhook.get("url"))
    print("Webhook: включён; получение через getUpdates невозможно." if active else "Webhook: выключен.")
    return active


def send_message(chat_id, text: str, markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if markup:
        payload["reply_markup"] = markup
    return tg("sendMessage", payload)


def chatgpt_connected(chat_id: str) -> bool:
    return (ROOT / ".auth" / "telegram" / chat_id / "chatgpt.enc").is_file()


def ai_provider(chat_id: str) -> str | None:
    directory = ROOT / ".auth" / "telegram" / chat_id
    try:
        selected = (directory / "provider").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        selected = "chatgpt"
    if selected == "api" and (directory / "api-key.enc").is_file():
        return "api"
    if selected == "chatgpt" and chatgpt_connected(chat_id):
        return "chatgpt"
    return None


def account_buttons():
    rows = [
        [{"text": "Подключить OpenAI API", "callback_data": "connect_api"}],
        [{"text": "Сбросить аккаунт", "callback_data": "reset_account"}],
    ]
    if MINIAPP_URL.startswith("https://"):
        rows.insert(0, [{"text": "Открыть Маркетолог°", "web_app": {"url": MINIAPP_URL}}])
    return rows


def prompt_chatgpt(chat_id: str, prefix: str = ""):
    markup = {"inline_keyboard": [
        [{"text": "Подключить ChatGPT", "callback_data": "connect_chatgpt"}],
        *account_buttons(),
    ]}
    send_message(chat_id, prefix + "Выберите, как подключить генерацию постов. Вход в ChatGPT проходит в браузере на компьютере с ботом. Ключ OpenAI API можно подключить с телефона.", markup)


def request_account_reset(chat_id: str):
    token = uuid.uuid4().hex
    reset_requests[chat_id] = token
    markup = {"inline_keyboard": [[
        {"text": "Да, сбросить", "callback_data": f"reset_confirm:{token}"},
        {"text": "Отмена", "callback_data": f"reset_cancel:{token}"},
    ]]}
    send_message(chat_id, "Сброс удалит профиль, черновики, аналитику и сохранённые подключения к ChatGPT и OpenAI API. Уже опубликованные посты останутся в канале. Подтвердите сброс, если хотите зарегистрироваться заново.", markup)


def reset_account(chat_id: str):
    auth_root = ROOT / ".auth" / "telegram"
    auth_dir = auth_root / chat_id
    if not re.fullmatch(r"[1-9][0-9]{0,15}", chat_id) or auth_dir.resolve() != auth_dir or not auth_dir.is_relative_to(ROOT):
        raise SetupError("Не удалось безопасно удалить сохранённое подключение.")
    api_key_requests.discard(chat_id)
    with chatgpt_logins_lock:
        process = chatgpt_logins.get(chat_id)
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        chatgpt_logins.pop(chat_id, None)
    if auth_dir.exists():
        shutil.rmtree(auth_dir)
    legacy_path = ROOT / "accounts.json"
    legacy = read_json(legacy_path, {})
    if chat_id in legacy:
        del legacy[chat_id]
        write_json(legacy_path, legacy)
    user_database.delete_account(chat_id)
    workspaces.pop(chat_id, None)
    for section in ("setup", "uploads", "chats"):
        state.get(section, {}).pop(chat_id, None)
    drafts = state.get("drafts", {})
    for draft_id, draft in list(drafts.items()):
        if draft.get("chat_id") == chat_id:
            del drafts[draft_id]
    groups = state.get("media_groups", {})
    for group_id in list(groups):
        if group_id.startswith(f"{chat_id}:"):
            del groups[group_id]
    if chat_id == ADMIN_CHAT_ID:
        state["legacy_profile_reset"] = True
    write_json(STATE_FILE, state)


def start_api_login(chat_id: str):
    with chatgpt_logins_lock:
        active = chat_id in chatgpt_logins
    if active:
        send_message(chat_id, "Завершите вход в ChatGPT или дождитесь окончания срока ссылки, затем повторите /api.")
        return
    markup = {"inline_keyboard": [[{"text": "Создать ключ OpenAI", "url": "https://platform.openai.com/api-keys"}]]}
    send_message(chat_id, "Создайте ключ проекта OpenAI и отправьте его одним сообщением в этот личный чат. Бот удалит сообщение перед проверкой ключа. Отмена — /cancel.", markup)
    api_key_requests.add(chat_id)


def api_error(code: str) -> str:
    if code in ("invalid_key", "api_http_401"):
        return "OpenAI отклонил ключ. Создайте новый ключ проекта и попробуйте снова."
    if code in ("api_http_403", "api_http_404"):
        return "У ключа нет доступа к нужной модели или операции. Проверьте права ключа и доступ к модели в проекте OpenAI."
    if code in ("api_http_402", "api_http_429"):
        return "OpenAI API ограничил запрос. Проверьте баланс и лимиты проекта на platform.openai.com."
    return "OpenAI API сейчас недоступен. Повторите запрос через некоторое время."


def connect_api_key(chat_id: str, key: str, message_id):
    if not re.search(r"\bsk-[A-Za-z0-9_-]{16,500}", key):
        send_message(chat_id, "Нужен ключ проекта OpenAI, начинающийся с sk-. Пришлите ключ отдельным сообщением или отправьте /cancel.")
        return
    if message_id is None:
        return
    try:
        tg("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except (TelegramError, TimeoutError):
        send_message(chat_id, "Не удалось удалить сообщение. Удалите его и отправьте новый ключ через /api.")
        api_key_requests.discard(chat_id)
        return
    try:
        save_openai_key(chat_id, key)
    except SetupError as exc:
        send_message(chat_id, str(exc) + " Пришлите ключ ещё раз или отправьте /cancel.")
        return
    api_key_requests.discard(chat_id)
    send_message(chat_id, "✅ OpenAI API подключён. Следующие запросы пойдут через ваш проект OpenAI. Напишите тему поста.")


def save_openai_key(chat_id: str, key: str):
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,500}", key) or key.startswith("sk-admin-"):
        raise SetupError("Нужен ключ проекта OpenAI, начинающийся с sk-.")
    with chatgpt_logins_lock:
        if chat_id in chatgpt_logins:
            raise SetupError("Дождитесь завершения входа в ChatGPT, затем подключите ключ OpenAI API.")
    try:
        result = subprocess.run(
            ["node", "siwc.mjs", "connect-api", chat_id], cwd=ROOT,
            input=json.dumps({"apiKey": key}), capture_output=True,
            text=True, encoding="utf-8", timeout=50,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError(api_error("")) from None
    if result.returncode:
        raise SetupError(api_error(result.stderr.strip()))


def chatgpt_login_error(stderr: str) -> str:
    match = re.search(r"(?:^|\n)oauth_error:([a-z_]+)(?:\r?\n|$)", stderr or "")
    code = match.group(1) if match else ""
    messages = {
        "invalid_grant": "Временный код входа OpenAI истёк или уже использован. Нажмите «Подключить ChatGPT» и завершите вход по новой ссылке.",
        "access_denied": "Вход отменён или разрешение не подтверждено. Нажмите «Подключить ChatGPT» и разрешите использование плана.",
        "permission_missing": "ChatGPT не выдал разрешение на использование плана. Подключитесь снова и подтвердите доступ к тарифу.",
        "secure_storage": "Windows не смог безопасно сохранить вход. Запустите бота от того же пользователя Windows и проверьте PowerShell.",
        "registration": "OpenAI не завершил регистрацию подключения. Запустите вход ещё раз по новой ссылке.",
        "network": "Не удалось связаться с OpenAI при завершении входа. Проверьте интернет и подключитесь по новой ссылке.",
    }
    return messages.get(code, "Не удалось завершить вход в ChatGPT. Нажмите «Подключить ChatGPT» и используйте новую ссылку.")


def start_chatgpt_login(chat_id: str):
    api_key_requests.discard(chat_id)
    with chatgpt_logins_lock:
        active = chatgpt_logins.get(chat_id)
        if active:
            send_message(chat_id, "Подключение уже начато. Откройте выданную ссылку в Telegram Desktop на компьютере с ботом.")
            return
        try:
            process = subprocess.Popen(
                ["node", "siwc.mjs", "login-telegram", chat_id], cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
            )
        except OSError:
            send_message(chat_id, "Не удалось запустить подключение ChatGPT. Попробуйте позже.")
            return
        chatgpt_logins[chat_id] = process

    def finish_login():
        def release_login():
            with chatgpt_logins_lock:
                if chatgpt_logins.get(chat_id) is process:
                    chatgpt_logins.pop(chat_id, None)
                    return True
                return False

        def stop_process():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        try:
            result = json.loads(process.stdout.readline())
            link = result["url"]
            markup = {"inline_keyboard": [[{"text": "Войти в ChatGPT", "url": link}]]}
            send_message(chat_id, "Откройте ссылку в Telegram Desktop на компьютере с ботом. Войдите в ChatGPT и разрешите использование своего плана. Ссылка действует 10 минут.", markup)
            exit_code = process.wait(timeout=660)
            error_output = process.stderr.read()
            if not release_login():
                return
            if exit_code == 0 and chatgpt_connected(chat_id):
                send_message(chat_id, "✅ ChatGPT подключён. Напишите, какой пост подготовить, или отправьте /post с темой.")
            else:
                prompt_chatgpt(chat_id, chatgpt_login_error(error_output) + "\n\n")
        except (ValueError, KeyError, OSError, TelegramError, subprocess.TimeoutExpired):
            stop_process()
            error_output = ""
            if process.stderr:
                try:
                    error_output = process.stderr.read()
                except OSError:
                    pass
            if not release_login():
                return
            try:
                prompt_chatgpt(chat_id, chatgpt_login_error(error_output) + "\n\n")
            except TelegramError:
                pass
        finally:
            stop_process()
            for stream in (process.stdout, process.stderr):
                if stream:
                    stream.close()
            release_login()

    threading.Thread(target=finish_login, daemon=True).start()


def send_photo(chat_id, image: bytes, caption: str, markup: dict):
    boundary = uuid.uuid4().hex
    fields = {"chat_id": str(chat_id), "caption": caption, "reply_markup": json.dumps(markup, ensure_ascii=False)}
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8"))
    extension, mime = ("png", "image/png") if image.startswith(b"\x89PNG") else ("jpg", "image/jpeg")
    parts.extend([
        f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="post.{extension}"\r\nContent-Type: {mime}\r\n\r\n'.encode(),
        image, f'\r\n--{boundary}--\r\n'.encode(),
    ])
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise TelegramError("Telegram отклонил загрузку изображения.", exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TelegramError("Не удалось подтвердить загрузку изображения.") from None
    if not result.get("ok"):
        raise TelegramError("Telegram не принял изображение.", result.get("error_code"))
    return result["result"]


def text_length(text):
    return len(text.encode("utf-16-le")) // 2


def is_clarification(text: str, *, short_question: bool = True) -> bool:
    opening = text.lstrip(" \n#>*«\"").casefold()
    return bool(re.match(
        r"(?:пришлите|уточните|расскажите, пожалуйста|пожалуйста,? (?:пришлите|уточните)|"
        r"в ч[её]м заключал[ао]сь|чтобы (?:подготовить|написать|составить) (?:пост|публикацию)|"
        r"мне (?:нужно|нужны|не хватает))\b", opening,
    ) or (short_question and opening.endswith("?") and not re.search(r"[.!?]\s", opening[:-1])))


def request_text(chat_id: str, instructions: str, prompt: str) -> str:
    with chatgpt_logins_lock:
        if chat_id in chatgpt_logins:
            raise SetupError("Завершите подключение ChatGPT в браузере и повторите запрос.")
    provider = ai_provider(chat_id)
    if provider is None:
        raise SetupError("Для генерации подключите ChatGPT через /chatgpt или OpenAI API через /api.")
    today = bot_time().date().isoformat()
    usage = state.setdefault("usage", {})
    if usage.get("date") != today:
        usage.clear()
        usage.update(date=today, total=0, users={})
    count = usage["users"].get(chat_id, 0)
    if usage["total"] >= DAILY_AI_LIMIT or count >= DAILY_USER_LIMIT:
        raise SetupError("Дневной лимит AI-запросов исчерпан. Попробуйте завтра.")
    usage["users"][chat_id] = count + 1
    usage["total"] += 1
    write_json(STATE_FILE, state)
    finished = threading.Event()

    def show_typing():
        while not finished.is_set():
            try:
                tg("sendChatAction", {"chat_id": chat_id, "action": "typing"})
            except Exception:
                pass
            if finished.wait(4):
                break

    typing = threading.Thread(target=show_typing, daemon=True)
    typing.start()
    try:
        request = subprocess.run(
            ["node", "siwc.mjs", "infer-api" if provider == "api" else "infer", chat_id], cwd=ROOT,
            input=json.dumps({"prompt": prompt, "instructions": instructions}, ensure_ascii=False),
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
    finally:
        finished.set()
        typing.join(timeout=1)
    if request.returncode:
        if provider == "api":
            raise SetupError(api_error(request.stderr.strip()) + " Чтобы заменить ключ, отправьте /api.")
        raise SetupError("ChatGPT не ответил. Попробуйте ещё раз. Для повторного подключения используйте /chatgpt.")
    answer = json.loads(request.stdout).get("text", "").strip()
    if not answer:
        raise RuntimeError("Модель вернула пустой ответ")
    return answer


def post_body(answer: str) -> str | None:
    body = re.sub(r"^```(?:text)?\s*|\s*```$", "", answer.strip(), flags=re.IGNORECASE)
    body = re.sub(r"^(?:ПОСТ|ГОТОВЫЙ ПОСТ)\s*:?[ \t]*\n?", "", body, count=1, flags=re.IGNORECASE).strip()
    body = re.sub(r"^(?:Конечно[!,.:]?\s*(?:вот\s+)?(?:готовый\s+)?пост[:.]?\s*|Вот готовый пост[:.]?\s*)", "", body, count=1, flags=re.IGNORECASE)
    if body and text_length(body) <= 900 and not is_clarification(body) and not any(is_clarification(p, short_question=False) for p in re.split(r"\n\s*\n", body)):
        return body
    return None


def openai_text(chat_id: str, user_text: str, extra: str = "", *, post: bool = False) -> str:
    profile = workspaces[chat_id]["profile"]
    saved_history = state["chats"].setdefault(str(chat_id), [])
    history = [] if post else saved_history[-8:]
    transcript = "\n".join(f"{item['role']}: {item['text']}" for item in history)
    task = (
        "Напиши готовый пост для Telegram как редактор, которому интересна сама тема. "
        "Выбери один угол подачи под задачу: неожиданная связь между известными фактами, конкретный вопрос читателю, "
        "небольшая история из известных деталей или практическая польза. В первой строке дай короткий заголовок, "
        "который подходит именно этому посту и связан с его темой. "
        "После заголовка оставь пустую строку. Раскрой тему в 2–4 коротких абзацах, чередуй короткие и более длинные "
        "фразы. Используй точные существительные и активные глаголы, называй участников и события по имени. "
        "Покажи, что в событии может зацепить аудиторию. Заверши запоминающейся фразой по теме или вопросом, "
        "на который читателю захочется ответить. "
        "До трёх уместных эмодзи по смыслу, без Markdown, повторов заголовка, канцелярита, "
        "шаблонных фраз «Рады сообщить» и «Это больше, чем просто», натянутых метафор и лишних хештегов. "
        "Если деталей мало, сократи пост и опусти неизвестные детали, включая условия задания, даты и результаты. "
        "Если известно только, что компания предоставила задание для мероприятия, этого достаточно для короткой публикации. "
        "Не задавай уточняющих вопросов заказчику, не добавляй заглушки и не выдумывай факты. "
        "Не более 800 символов, включая пробелы. Начни ответ с отдельной строки ПОСТ, затем напиши только текст для канала."
        if post else "Ответь на вопрос пользователя. При необходимости задай короткий уточняющий вопрос."
    )
    instructions = (
        f"Ты AI-маркетолог компании. Отвечай по-русски, естественно и по делу. {task}\n\n"
        f"ПРОФИЛЬ КОМПАНИИ:\n{json.dumps(profile, ensure_ascii=False)}\n\n"
        f"ПРЕДЫДУЩИЙ ДИАЛОГ:\n{transcript or 'пока нет'}"
    )
    prompt = (extra + "\n" if extra else "") + user_text
    answer = request_text(chat_id, instructions, prompt)
    if post:
        answer = post_body(answer)
        if answer is None:
            retry = instructions + "\n\nВерни строку ПОСТ и законченный текст до 800 символов. Сохрани конкретный заголовок, живой ритм и известные факты."
            answer = post_body(request_text(chat_id, retry, prompt))
        if answer is None:
            raise SetupError("Модель не вернула готовый пост. Попробуйте повторить запрос.")
    saved_history.extend([{"role": "user", "text": user_text}, {"role": "assistant", "text": answer}])
    state["chats"][str(chat_id)] = saved_history[-16:]
    write_json(STATE_FILE, state)
    return answer


def pending_for(chat_id: str):
    return next((draft for draft in reversed(list(state["drafts"].values()))
                 if draft["chat_id"] == str(chat_id) and draft["status"] == "pending"), None)


def source_buttons(source):
    if source and source.get("kind") == "web":
        author = re.sub(r"\s+", " ", source.get("author", "")).strip()
        license_name = source.get("license", "")
        label = f"Фото: {author} · {license_name}" if author and license_name not in ("CC0", "Public domain") else "Фото · источник"
        return [{"text": label[:64], "url": source["url"]}]
    return []


def draft_buttons(draft_id: str, has_image: bool = False, source: dict | None = None):
    rows = [[
        {"text": "✅ Одобрить и опубликовать", "callback_data": f"approve:{draft_id}"},
        {"text": "↩️ Отклонить", "callback_data": f"reject:{draft_id}"},
    ], [
        {"text": "🔎 Найти фото", "callback_data": f"webimage:{draft_id}"},
        {"text": "🎨 Сгенерировать", "callback_data": f"image:{draft_id}"},
    ]]
    if source_buttons(source):
        rows.append(source_buttons(source))
    if has_image:
        rows.append([{"text": "Только текст", "callback_data": f"noimage:{draft_id}"}])
    return {"inline_keyboard": rows}


def clear_draft_buttons(draft):
    if draft.get("preview_message_id"):
        try:
            tg("editMessageReplyMarkup", {"chat_id": draft["chat_id"], "message_id": draft["preview_message_id"], "reply_markup": {"inline_keyboard": []}})
        except TelegramError:
            pass


def send_draft(chat_id: str, text: str, replaces: dict | None = None, photo_id: str | None = None, photo_source: dict | None = None):
    draft_id = uuid.uuid4().hex
    channel = workspaces[chat_id]["channel"]
    label = workspaces[chat_id].get("channel_label", channel)
    text = text[:3500]
    markup = draft_buttons(draft_id, bool(photo_id), photo_source)
    if photo_id:
        if text_length(text) > 1024:
            raise SetupError("Для поста с картинкой сократите текст до 1024 символов.")
        sent = tg("sendPhoto", {"chat_id": chat_id, "photo": photo_id, "caption": text, "reply_markup": markup})
    else:
        sent = send_message(chat_id, f"📝 Черновик для {label}\n\n{text}\n\nПубликация сразу после одобрения. Для правок напишите «переделай: …».", markup)
    draft = {"chat_id": chat_id, "channel": channel, "text": text, "status": "pending", "preview_message_id": sent["message_id"]}
    if photo_id:
        draft["photo_id"] = photo_id
        draft["photo_source"] = photo_source or {"kind": "user"}
    state["drafts"][draft_id] = draft
    if replaces:
        replaces["status"] = "superseded"
    write_json(STATE_FILE, state)
    if replaces:
        clear_draft_buttons(replaces)
    return draft


def attach_image_bytes(chat_id, draft, image, source):
    if text_length(draft["text"]) > 1024:
        raise SetupError("Сначала сократите текст до 900 символов: «переделай: короче».")
    draft_id = uuid.uuid4().hex
    sent = send_photo(chat_id, image, draft["text"], draft_buttons(draft_id, True, source))
    result = {
        "chat_id": chat_id, "channel": draft["channel"], "text": draft["text"],
        "photo_id": sent["photo"][-1]["file_id"], "photo_source": source,
        "preview_message_id": sent["message_id"], "status": "pending",
    }
    state["drafts"][draft_id] = result
    draft["status"] = "superseded"
    write_json(STATE_FILE, state)
    clear_draft_buttons(draft)
    return result


def add_draft_image(chat_id: str, draft: dict, instruction: str = ""):
    if not images_configured(chat_id):
        raise SetupError("Для генерации изображения подключите OpenAI API через /api. Поиск лицензированных фото и публикация текста работают без API-ключа.")
    if text_length(draft["text"]) > 1024:
        raise SetupError("Сначала попросите сократить пост до 900 символов: «переделай: сократи до 900 символов».")
    today = bot_time().date().isoformat()
    usage = state.setdefault("image_usage", {})
    if usage.get("date") != today:
        usage.clear()
        usage.update(date=today, total=0, users={})
    count = usage["users"].get(chat_id, 0)
    if usage["total"] >= DAILY_IMAGE_LIMIT or count >= DAILY_USER_IMAGE_LIMIT:
        raise SetupError("Дневной лимит генерации картинок исчерпан. Текстовый черновик сохранён.")
    usage["users"][chat_id] = count + 1
    usage["total"] += 1
    write_json(STATE_FILE, state)
    send_message(chat_id, "🎨 Создаю иллюстрацию к посту. Это может занять несколько минут. Публикация будет только после вашего одобрения.")
    profile = json.dumps(workspaces[chat_id]["profile"], ensure_ascii=False)
    prompt = (
        "Создай качественную иллюстрацию для публикации компании в Telegram. "
        "Изображение должно соответствовать теме поста. Без текста, водяных знаков и выдуманных логотипов. "
        "Для событий используй концептуальную иллюстрацию, не выдавай её за документальное фото.\n"
        f"Компания: {profile}\nПост: {draft['text']}\nПожелания к изображению: {instruction[:2000]}"
    )
    image = generate_image(prompt, chat_id)
    return attach_image_bytes(chat_id, draft, image, {"kind": "generated"})


def read_model_json(answer):
    answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
    try:
        result = json.loads(answer)
    except ValueError:
        raise SetupError("Не удалось подобрать иллюстрацию. Попробуйте /findimage с описанием нужного фото.") from None
    if not isinstance(result, dict):
        raise SetupError("Не удалось подобрать иллюстрацию. Попробуйте /findimage с описанием нужного фото.")
    return result


def local_image_query(text, instruction=""):
    source = f"{instruction} {text.splitlines()[0] if text else ''} {text[:500]}"
    words = re.findall(r"[A-Za-z0-9А-Яа-яЁё-]{2,}", source.casefold())
    stop = {
        "напиши", "напишите", "пост", "картинка", "картинкой", "фото", "изображение", "иллюстрация",
        "компания", "компании", "наша", "нашей", "который", "которая", "которые", "что", "это", "для",
        "чтобы", "только", "может", "будет", "было", "были", "есть", "или", "как", "про", "всем", "нам",
        "подходящий", "подходящее", "создай", "сделай", "генерируй", "найди", "ищи", "please", "write",
        "create", "image", "photo", "post", "about", "with", "from", "this", "that", "into", "their",
    }
    translations = {
        "стекло": "glass", "стекла": "glass", "стекольный": "glass", "стекольная": "glass",
        "производство": "manufacturing", "завод": "factory", "завода": "factory",
        "команда": "team", "команды": "team", "хакатон": "hackathon", "хакатона": "hackathon",
        "программисты": "programmers", "программирование": "programming", "технологии": "technology",
        "архитектура": "architecture", "интерьер": "interior", "строительство": "construction",
        "экология": "environment", "природа": "nature", "офис": "office", "люди": "people",
        "сотрудники": "workers", "здание": "building", "проект": "project", "дизайн": "design",
    }
    unique = []
    for word in words:
        if word in stop or word.isdecimal() and len(word) < 3:
            continue
        translated = translations.get(word, word)
        if translated not in unique:
            unique.append(translated)
        if len(unique) == 6:
            break
    return " ".join(unique)


def add_web_image(chat_id, draft, instruction="", fallback=True):
    send_message(chat_id, "🔎 Подбираю иллюстрацию к посту. Прикреплённое вами фото можно использовать вместо неё.")
    query = local_image_query(draft.get("text", ""), instruction)
    if not query:
        raise SetupError("Не смог выделить тему для поиска. Добавьте в запрос несколько слов о нужной сцене.")
    try:
        candidates = search_images(query)
    except WebImageError as exc:
        candidates = []
        search_error = str(exc)
    else:
        search_error = ""
    old_source = draft.get("photo_source", {}).get("url")
    candidates = [item for item in candidates if item["url"] != old_source]
    if candidates:
        selected = candidates[0]
        try:
            image = download_image(selected["download_url"])
        except WebImageError as exc:
            search_error = str(exc)
        else:
            source = {key: value for key, value in selected.items() if key != "download_url"}
            return attach_image_bytes(chat_id, draft, image, source)
    if fallback and images_configured(chat_id):
        send_message(chat_id, "В открытых фотобанках не нашёл подходящего файла. Создам авторскую иллюстрацию через подключённый API.")
        return add_draft_image(chat_id, draft, instruction)
    if fallback:
        suffix = f" Поиск: {search_error}" if search_error else ""
        raise SetupError("Не нашёл свободно используемое фото. Отправьте своё изображение или подключите OpenAI API для генерации." + suffix)
    suffix = f" Поиск: {search_error}" if search_error else ""
    raise SetupError("Не нашёл подходящее лицензированное фото. Уточните описание, пришлите своё фото или подключите OpenAI API." + suffix)


def prepare_post(chat_id, text, request, replaces=None, upload=None):
    if upload:
        if upload["kind"] == "photo":
            return send_draft(chat_id, text, replaces=replaces, photo_id=upload["file_id"], photo_source={"kind": "user"})
        return attach_uploaded_document(chat_id, text, replaces, upload)
    if re.search(r"без\s+(?:фото|картин\w*|изображени\w*|иллюстраци\w*)|только\s+текст", request.lower()):
        return send_draft(chat_id, text, replaces=replaces)
    draft = {"channel": workspaces[chat_id]["channel"], "text": text, "chat_id": chat_id, "status": "pending"}
    try:
        if re.search(r"(?:сгенерируй|нарисуй)", request.lower()):
            result = add_draft_image(chat_id, draft, request)
        else:
            result = add_web_image(chat_id, draft, request)
    except (SetupError, ImageError, WebImageError) as exc:
        result = send_draft(chat_id, text, replaces=replaces)
        send_message(chat_id, str(exc))
        return result
    except (TelegramError, TimeoutError):
        result = send_draft(chat_id, text, replaces=replaces)
        send_message(chat_id, "Не удалось подтвердить доставку картинки. Текст сохранён; повторите добавление фото.")
        return result
    except Exception:
        send_draft(chat_id, text, replaces=replaces)
        raise SetupError("Текст сохранён. Не удалось подобрать иллюстрацию; можно добавить своё фото или повторить поиск.") from None
    if replaces:
        replaces["status"] = "superseded"
        write_json(STATE_FILE, state)
        clear_draft_buttons(replaces)
    return result


def download_user_image(upload):
    info = tg("getFile", {"file_id": upload["file_id"]})
    path = info.get("file_path", "")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or ".." in path or path.startswith("/"):
        raise SetupError("Не удалось прочитать файл. Пришлите изображение как фото.")
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}", timeout=30) as response:
            image = response.read(10 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SetupError("Не удалось загрузить ваш файл. Попробуйте отправить его как фото.") from None
    if len(image) > 10 * 1024 * 1024 or not (image.startswith(b"\xff\xd8\xff") or image.startswith(b"\x89PNG\r\n\x1a\n")):
        raise SetupError("Пришлите JPEG или PNG до 10 МБ либо отправьте изображение как фото.")
    return image


def attach_uploaded_document(chat_id, text, replaces, upload):
    draft = {"channel": workspaces[chat_id]["channel"], "text": text}
    result = attach_image_bytes(chat_id, draft, download_user_image(upload), {"kind": "user"})
    if replaces:
        replaces["status"] = "superseded"
        write_json(STATE_FILE, state)
        clear_draft_buttons(replaces)
    return result


def private_chat_id(message: dict, sender: dict) -> str | None:
    chat = message.get("chat", {})
    if chat.get("type") == "private" and chat.get("id") == sender.get("id") and sender.get("id"):
        return str(chat["id"])
    return None


def valid_email(value: str) -> bool:
    return len(value) <= 254 and bool(re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]{2,}", value))


def password_record(password: str) -> dict:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
    return {"salt": salt.hex(), "hash": digest.hex(), "algorithm": "pbkdf2_sha256", "iterations": 600_000}


def delete_private_message(chat_id: str, message_id):
    if message_id is None:
        return
    try:
        tg("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except Exception:
        pass


def claim_email(chat_id: str, email: str, password: str):
    normalized = email.strip().casefold()
    if user_database.email_exists(normalized, chat_id):
        raise SetupError("Этот email уже зарегистрирован. Используйте другой адрес или напишите владельцу сервиса для восстановления доступа.")
    user_database.save(chat_id, normalized, password_record(password))


def channel_address(text: str) -> str:
    if re.fullmatch(r"-100[0-9]+", text):
        return text
    match = re.fullmatch(r"(?:@|(?:https?://)?t\.me/)([A-Za-z0-9_]{4,32})/?", text)
    if match:
        return "@" + match[1]
    raise SetupError("Пришлите @имя_канала, ссылку t.me/имя_канала или числовой ID закрытого канала.")


def check_channel(channel: str, chat_id: str) -> dict:
    global BOT_ID
    info = tg("getChat", {"chat_id": channel})
    if info["type"] != "channel":
        raise SetupError("Укажите Telegram-канал.")
    if BOT_ID is None:
        BOT_ID = tg("getMe")["id"]
    bot = tg("getChatMember", {"chat_id": info["id"], "user_id": BOT_ID})
    if bot["status"] != "administrator" or not bot.get("can_post_messages"):
        raise SetupError("Добавьте бота в администраторы канала с правом публикации и попробуйте снова.")
    member = tg("getChatMember", {"chat_id": info["id"], "user_id": int(chat_id)})
    if member["status"] != "creator" and not (member["status"] == "administrator" and member.get("can_post_messages")):
        raise SetupError("Для подключения канала у вашего аккаунта должно быть право публикации в нём.")
    return info


def start_setup(chat_id: str, sender: dict | None = None):
    api_key_requests.discard(chat_id)
    workspace = workspaces.get(chat_id, {})
    registered = workspace.get("registration", {})
    account = user_database.get(chat_id) or {}
    has_account = bool(account or registered.get("email"))
    first_step = "name" if has_account else "email"
    setup = {"step": first_step, "mode": "edit" if has_account else "registration", "telegram_username": (sender or {}).get("username", "")}
    if has_account:
        setup["email"] = account.get("email", registered.get("email", ""))
    state.setdefault("setup", {})[chat_id] = setup
    write_json(STATE_FILE, state)
    send_setup_prompt(chat_id, setup)


def step_number(setup: dict, step: str) -> str:
    if setup.get("mode") == "edit":
        order = {"name": 1, "company": 2, "description": 3, "channel": 4}
        return f"{order[step]}/4"
    order = {"email": 1, "password": 2, "name": 3, "company": 4, "description": 5, "channel": 6}
    return f"{order[step]}/6"


def flow_title(setup: dict) -> str:
    return "Изменение профиля" if setup.get("mode") == "edit" else "Регистрация"


def send_setup_prompt(chat_id: str, setup: dict, prefix: str = ""):
    questions = {
        "email": "Введите email для аккаунта. Затем бот попросит создать пароль.\n\nНе используйте пароль от почты или других сервисов. Отмена — /cancel.",
        "password": "Создайте пароль длиной от 12 до 128 символов. Не используйте пароль от почты или других сервисов.",
        "name": "Как к вам обращаться? Пришлите имя (до 80 символов).",
        "company": "Как называется ваша компания? Пришлите только название.",
        "description": "Чем занимается компания, кто ваши клиенты и какой стиль постов нужен? До 2000 символов.",
        "channel": "Добавьте меня в администраторы канала с правом публикации. Пришлите @имя_канала, ссылку t.me/имя_канала или ID закрытого канала вида -100…",
    }
    step = setup["step"]
    send_message(chat_id, f"{flow_title(setup)} · {step_number(setup, step)}\n{prefix}{questions[step]}")


def setup_step(chat_id: str, text: str, setup: dict):
    step = setup.get("step")
    if step not in ("email", "password", "name", "company", "description", "channel"):
        start_setup(chat_id)
        return
    limits = {"name": 80, "company": 150, "description": 2000}
    if step == "email":
        email = text.strip().casefold()
        if not valid_email(email):
            send_message(chat_id, "Email выглядит неверно. Введите адрес вида name@example.com.")
            return
        if user_database.email_exists(email, chat_id):
            send_message(chat_id, "Этот email уже зарегистрирован. Введите другой адрес или обратитесь к владельцу сервиса.")
            return
        setup.update(email=email, step="password")
        write_json(STATE_FILE, state)
        send_setup_prompt(chat_id, setup)
        return
    if step == "password":
        delete_private_message(chat_id, setup.pop("current_message_id", None))
        if len(text) < 12 or len(text) > 128:
            send_message(chat_id, "Пароль должен быть длиной от 12 до 128 символов. Пришлите новый пароль отдельным сообщением.")
            return
        try:
            claim_email(chat_id, setup["email"], text)
        except SetupError as exc:
            setup["step"] = "email"
            write_json(STATE_FILE, state)
            send_setup_prompt(chat_id, setup, f"{exc}\n")
            return
        setup.update(step="name")
        write_json(STATE_FILE, state)
        send_setup_prompt(chat_id, setup, "Пароль сохранён. ")
        return
    if step in limits and (not text.strip() or len(text) > limits[step]):
        send_message(chat_id, f"Пришлите непустой текст до {limits[step]} символов.")
        return
    if step == "name":
        setup.update(display_name=text, step="company")
        write_json(STATE_FILE, state)
        send_setup_prompt(chat_id, setup)
        return
    if step == "company":
        setup.update(company_name=text, step="description")
        write_json(STATE_FILE, state)
        send_setup_prompt(chat_id, setup)
        return
    if step == "description":
        setup.update(description=text, step="channel")
        write_json(STATE_FILE, state)
        send_setup_prompt(chat_id, setup)
        return
    try:
        channel = check_channel(channel_address(text), chat_id)
    except SetupError as exc:
        send_message(chat_id, str(exc))
        return
    except (TelegramError, TimeoutError):
        send_message(chat_id, "Не удалось проверить канал. Проверьте адрес и права бота, затем пришлите адрес ещё раз.")
        return
    workspace = workspaces.setdefault(chat_id, {})
    registered_at = workspace.get("registration", {}).get("registered_at", bot_time().isoformat())
    account = user_database.get(chat_id) or {}
    email = account.get("email", setup.get("email", ""))
    workspace.update(
        channel=str(channel["id"]),
        channel_label=("@" + channel["username"]) if channel.get("username") else channel.get("title", str(channel["id"])),
        profile={"name": setup["company_name"], "description": setup["description"]},
        registration={"display_name": setup["display_name"], "telegram_user_id": chat_id, "email": email,
                      "telegram_username": setup.get("telegram_username", ""), "registered_at": registered_at},
    )
    user_database.save(
        chat_id, email, {}, display_name=setup["display_name"],
        telegram_username=setup.get("telegram_username", ""),
        company_name=setup["company_name"], company_description=setup["description"],
        channel_id=str(channel["id"]), channel_label=workspace["channel_label"],
        registered_at=registered_at,
    )
    state["setup"].pop(chat_id)
    state.setdefault("uploads", {}).pop(chat_id, None)
    state["chats"].pop(chat_id, None)
    retire_drafts(chat_id)
    write_json(STATE_FILE, state)
    summary = f"✅ {setup['display_name']}, {('профиль обновлён' if setup.get('mode') == 'edit' else 'регистрация завершена')}!\nКомпания: {setup['company_name']}\nКанал: {workspace['channel_label']}\n\n"
    if ai_provider(chat_id):
        send_message(chat_id, summary + "Напишите, какой пост подготовить.", {"inline_keyboard": account_buttons()})
    else:
        prompt_chatgpt(chat_id, summary)


@with_bot_state
def handle_message(message: dict):
    chat_id = private_chat_id(message, message.get("from", {}))
    text = (message.get("text") or message.get("caption") or "").strip()
    if not chat_id:
        return
    if re.search(r"\bsk-[A-Za-z0-9_-]{16,500}", text):
        if chat_id in api_key_requests and (message.get("text") or "").strip() == text:
            connect_api_key(chat_id, text, message.get("message_id"))
        else:
            delete_private_message(chat_id, message.get("message_id"))
            send_message(chat_id, "Для подключения ключа сначала отправьте /api, затем пришлите ключ отдельным сообщением.")
        return
    upload = None
    photos = message.get("photo") or []
    document = message.get("document") or {}
    if photos:
        upload = {"kind": "photo", "file_id": photos[-1]["file_id"]}
    elif document:
        if document.get("mime_type") not in ("image/jpeg", "image/png") or document.get("file_size", 0) > 10 * 1024 * 1024:
            send_message(chat_id, "Пришлите картинку как фото либо JPEG/PNG до 10 МБ.")
            return
        upload = {"kind": "document", "file_id": document["file_id"]}
    if not text and not upload:
        return
    if upload and message.get("media_group_id"):
        groups = state.setdefault("media_groups", {})
        key = f"{chat_id}:{message['media_group_id']}"
        if key in groups:
            if not groups[key]:
                send_message(chat_id, "В одном посте пока поддерживается одно фото. Из альбома использовано первое; другое отправьте отдельно.")
                groups[key] = True
                write_json(STATE_FILE, state)
            return
        groups[key] = False
        while len(groups) > 100:
            groups.pop(next(iter(groups)))
        write_json(STATE_FILE, state)
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
    workspace = workspaces.get(chat_id)
    if command == "/start" and text.split(maxsplit=1)[1:] == ["chatgpt"] and (workspace or {}).get("registration"):
        start_chatgpt_login(chat_id)
        return
    if command in ("/ai", "/api"):
        if not (workspace or {}).get("registration") or chat_id in state.get("setup", {}):
            send_message(chat_id, "Сначала завершите регистрацию или изменение профиля.")
        elif command == "/api":
            start_api_login(chat_id)
        else:
            prompt_chatgpt(chat_id)
        return
    if command == "/chatgpt":
        if not (workspace or {}).get("registration"):
            send_message(chat_id, "Сначала завершите регистрацию. Для начала отправьте /start.")
        else:
            start_chatgpt_login(chat_id)
        return
    current_setup = state.get("setup", {}).get(chat_id)
    if command == "/start" and current_setup and current_setup.get("step") in ("email", "password", "name", "company", "description", "channel"):
        send_setup_prompt(chat_id, current_setup)
        return
    if command in ("/setup", "/register") or (command == "/start" and not (workspace or {}).get("registration")):
        start_setup(chat_id, message.get("from"))
        return
    if command == "/cancel":
        api_key_requests.discard(chat_id)
        reset_requests.pop(chat_id, None)
        state.setdefault("setup", {}).pop(chat_id, None)
        state.setdefault("uploads", {}).pop(chat_id, None)
        write_json(STATE_FILE, state)
        send_message(chat_id, "Настройка и отложенное фото отменены. Сохранённый профиль и черновик остались. Начать настройку — /setup.")
        return
    if command == "/whoami":
        send_message(chat_id, f"Ваш Telegram chat ID: {chat_id}. Бот определяет его автоматически.")
        return
    if command in ("/start", "/help"):
        if not workspace:
            start_setup(chat_id)
            return
        if command == "/start" and not ai_provider(chat_id):
            prompt_chatgpt(chat_id)
            return
        send_message(chat_id, "Напишите задачу или /post тема. Можно прикрепить фото с подписью: оно попадёт в пост. Без вашего фото подберу иллюстрацию из интернета, при необходимости сгенерирую. Для поста без фото добавьте «только текст».\n/register — регистрация\n/profile — мой профиль\n/setup — изменить данные\n/ai — способ подключения\n/chatgpt — подключить ChatGPT\n/api — подключить ключ OpenAI\n/analytics — публикации, реакции и комментарии\n/findimage описание — поиск фото к черновику\n/image описание — генерация картинки\n/schedule — расписание\n/status — статус черновика\n/cancel — отменить настройку или отложенное фото\n«переделай: …» — изменить последний черновик", {"inline_keyboard": account_buttons()})
        return
    if command == "/profile":
        if not workspace or not workspace.get("registration"):
            start_setup(chat_id, message.get("from"))
            return
        registration = workspace["registration"]
        profile = workspace["profile"]
        send_message(chat_id, f"Ваш профиль\nИмя: {registration['display_name']}\nEmail: {registration.get('email', '')}\nКомпания: {profile.get('name', '')}\nОписание: {profile.get('description', '')}\nКанал: {workspace.get('channel_label', workspace['channel'])}\nИзменить — /setup.", {"inline_keyboard": account_buttons()})
        return
    if command == "/schedule":
        if not workspace:
            start_setup(chat_id)
            return
        parts = text.split(maxsplit=1)
        setting = parts[1].strip().lower() if len(parts) == 2 else ""
        if setting == "off":
            workspace.pop("schedule", None)
        elif re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", setting):
            workspace["schedule"] = setting
        else:
            current = workspace.get("schedule", "выключено")
            send_message(chat_id, f"Расписание: {current} ({TIMEZONE}).\n/schedule 09:00 — готовить черновик ежедневно\n/schedule off — выключить.")
            return
        write_json(STATE_FILE, state)
        send_message(chat_id, f"Расписание: {workspace.get('schedule', 'выключено')} ({TIMEZONE}).")
        return
    if command == "/status":
        draft = pending_for(chat_id)
        if not workspace:
            send_message(chat_id, "Профиль компании ещё не настроен. Начните с /setup.")
            return
        profile = workspace["profile"]
        company = profile.get("name") or profile.get("description", "").splitlines()[0]
        channel = workspace.get("channel_label", workspace["channel"])
        draft_status = "Есть черновик на согласовании." if draft else "Черновиков на согласовании нет."
        send_message(chat_id, f"Канал: {channel}\nПрофиль компании: {company[:300]}\n{draft_status}")
        return
    if command == "/analytics":
        if not workspace:
            start_setup(chat_id, message.get("from"))
            return
        report = user_database.analytics(chat_id, limit=5)
        if not report["total_posts"]:
            send_message(chat_id, "Аналитика появится после публикации постов через бота или сайт.")
            return
        lines = [f"📊 Аналитика {workspace.get('channel_label', workspace['channel'])}",
                 f"Всего публикаций через бота: {report['total_posts']}",
                 "Подписчики используются как знаменатель для долей реакций и комментариев.",
                 "Просмотры, пересылки и клики Bot API не отдаёт. Комментарии идут с момента добавления бота в группу обсуждений.",
                 "Оценка качества комментариев — эвристика. Динамика собирается с запуска.", "", "Последние 5 публикаций:"]
        for post in report["posts"]:
            day = post["published_at"][:10]
            reactions = " · ".join(
                f"{item.get('type', {}).get('emoji', 'реакция')} {item.get('total_count', 0)}"
                for item in post["reactions"]
            )
            reaction_total = post["reaction_total"] if post["reaction_total"] is not None else "нет данных"
            def duration_label(seconds):
                if seconds is None:
                    return "нет данных"
                minutes = max(0, seconds) // 60
                return f"{minutes // 60} ч {minutes % 60} мин" if minutes >= 60 else f"{minutes} мин"
            rate = f"{post['reaction_rate_pct']}%" if post["reaction_rate_pct"] is not None else "нет базы подписчиков"
            comment_rate = f"{post['comment_rate_pct']}%" if post["comment_rate_pct"] is not None else "нет базы подписчиков"
            substantive = f"{post['substantive_comment_pct']}%" if post["substantive_comment_pct"] is not None else "нет комментариев"
            lines.append(
                f"{day} · {reaction_total} реакций · {post['comment_total']} комментариев"
                f"\nРеакции/подписчики: {rate} · Комментарии/подписчики: {comment_rate}"
                f"\nСодержательные комментарии: {substantive} · короткие/эмодзи: {post['short_comments']} · спам-подобные: {post['spam_comments']}"
                f"\nПервая реакция: {duration_label(post['first_reaction_seconds'])}"
                f" · Первый комментарий: {duration_label(post['first_comment_seconds'])}"
                f"\nСкорость по событиям: 15 мин — {post['reaction_velocity_15m'] if post['reaction_velocity_15m'] is not None else 'нет данных'}"
                f" · 1 ч — {post['reaction_velocity_60m'] if post['reaction_velocity_60m'] is not None else 'нет данных'}"
                f"\nПик прироста: {duration_label(post['peak_after_seconds'])}"
                f" · Всплеск: {'проверьте' if post['possible_spike'] else 'не замечен'}"
                f" · Половина реакций: {duration_label(post['half_life_seconds'])}"
                f" · После 24 ч: {post['long_tail_reactions'] if post['long_tail_reactions'] is not None else 'нет данных'}"
                f"\nПодписчики: {post['subscribers_at_publish'] if post['subscribers_at_publish'] is not None else 'нет снимка'}"
                f" · Δ1 ч: {post['subscribers_delta_1h'] if post['subscribers_delta_1h'] is not None else 'нет данных'}"
                f" · Δ24 ч: {post['subscribers_delta_24h'] if post['subscribers_delta_24h'] is not None else 'нет данных'}"
                f"{(': ' + reactions) if reactions else ''}\n{post['text_preview']}"
            )
        send_message(chat_id, "\n".join(lines))
        return
    if chat_id in api_key_requests and not command:
        if not upload:
            connect_api_key(chat_id, text, message.get("message_id"))
        return
    if command and command not in ("/post", "/image", "/findimage"):
        send_message(chat_id, "Доступные команды — /help.")
        return
    setup = state.get("setup", {}).get(chat_id)
    if setup is not None:
        if upload:
            send_message(chat_id, "Сначала завершите регистрацию текстовыми ответами. Затем пришлите фото с заданием для поста.")
            return
        if command:
            send_message(chat_id, "Сначала завершите регистрацию или отмените её командой /cancel.")
            return
        if setup.get("step") == "password":
            setup["current_message_id"] = message.get("message_id")
            write_json(STATE_FILE, state)
        setup_step(chat_id, text, setup)
        return
    if not workspace:
        start_setup(chat_id)
        return
    if not ai_provider(chat_id):
        prompt_chatgpt(chat_id)
        return
    try:
        existing = pending_for(chat_id)
        if upload and not text:
            if existing:
                prepare_post(chat_id, existing["text"], "", replaces=existing, upload=upload)
            else:
                state.setdefault("uploads", {})[chat_id] = upload
                write_json(STATE_FILE, state)
                send_message(chat_id, "Фото сохранено. Теперь напишите, о чём сделать пост — использую именно это изображение. Отмена — /cancel.")
            return
        waiting_upload = state.get("uploads", {}).get(chat_id)
        if not upload and not command:
            upload = waiting_upload
        if command == "/post" and not upload:
            upload = waiting_upload
        if not upload and (command in ("/image", "/findimage") or re.match(r"^(?:нарисуй|(?:сделай|создай|сгенерируй|найди|подбери)\s+(?:фото|картин\w*|изображени\w*|иллюстраци\w*))\b", text.lower())):
            if not existing:
                send_message(chat_id, "Сначала создайте пост: /post тема публикации. Затем добавьте к нему картинку.")
                return
            instruction = text.partition(" ")[2] if command else text
            if command == "/findimage" or re.match(r"^(?:найди|подбери)", text.lower()):
                add_web_image(chat_id, existing, instruction, fallback=False)
            else:
                add_draft_image(chat_id, existing, instruction)
        elif existing and (text.lower().startswith("переделай") or text.lower().startswith("измени")):
            instruction = text.split(":", 1)[-1].strip()
            revised = openai_text(chat_id, "Внеси правки в предыдущий черновик.", f"ТЕКУЩИЙ ЧЕРНОВИК:\n{existing['text']}\n\nИНСТРУКЦИЯ:\n{instruction}", post=True)
            if upload:
                prepare_post(chat_id, revised, instruction, replaces=existing, upload=upload)
                state.setdefault("uploads", {}).pop(chat_id, None)
                write_json(STATE_FILE, state)
            else:
                send_draft(chat_id, revised, replaces=existing, photo_id=existing.get("photo_id"), photo_source=existing.get("photo_source"))
        else:
            wants_post = bool(upload) or command == "/post" or bool(re.search(r"\b(?:напиши|подготовь|составь|сделай|создай|придумай|нужен|нужна)\b.*\b(?:пост\w*|публикаци\w*|текст\w*)\b|^(?:пост|публикация)\b", text.lower(), re.S))
            if command == "/post":
                text = text.partition(" ")[2].strip()
                if not text:
                    send_message(chat_id, "Пример: /post о нашем участии в хакатоне Кибер 102")
                    return
            extra = "Пользователь приложил фото для публикации. Используй факты из его текста; не описывай содержимое фото, которое тебе не передано." if upload else ""
            answer = openai_text(chat_id, text, extra=extra, post=wants_post)
            if wants_post:
                prepare_post(chat_id, answer, text, replaces=existing, upload=upload)
                if upload:
                    state.setdefault("uploads", {}).pop(chat_id, None)
                    write_json(STATE_FILE, state)
            else:
                send_message(chat_id, answer)
    except (SetupError, ImageError, WebImageError) as exc:
        send_message(chat_id, str(exc))
    except Exception as exc:
        print(f"Message handling failed: {exc}")
        send_message(chat_id, "Не получилось обработать запрос. Проверьте настройки API и попробуйте ещё раз.")


@with_bot_state
def handle_callback(query: dict):
    data = query.get("data", "")
    chat_id = private_chat_id(query.get("message") or {}, query.get("from", {}))
    tg("answerCallbackQuery", {"callback_query_id": query["id"]})
    if not chat_id:
        return
    if data == "reset_account":
        request_account_reset(chat_id)
        return
    reset_action, _, reset_token = data.partition(":")
    if reset_action in ("reset_confirm", "reset_cancel"):
        if reset_requests.get(chat_id) != reset_token:
            return
        reset_requests.pop(chat_id)
        try:
            tg("editMessageReplyMarkup", {
                "chat_id": chat_id, "message_id": query["message"]["message_id"],
                "reply_markup": {"inline_keyboard": []},
            })
        except TelegramError:
            pass
        if reset_action == "reset_cancel":
            send_message(chat_id, "Сброс отменён.", {"inline_keyboard": account_buttons()})
            return
        try:
            reset_account(chat_id)
        except Exception as exc:
            print(f"Account reset failed: {type(exc).__name__}")
            send_message(chat_id, "Не удалось завершить сброс. Нажмите «Сбросить аккаунт», чтобы повторить.", {"inline_keyboard": account_buttons()})
            return
        send_message(chat_id, "Аккаунт сброшен. Начнём регистрацию заново.")
        start_setup(chat_id, query["from"])
        return
    if data == "connect_api":
        if (workspaces.get(chat_id) or {}).get("registration") and chat_id not in state.get("setup", {}):
            start_api_login(chat_id)
        else:
            send_message(chat_id, "Сначала завершите регистрацию или изменение профиля.")
        return
    if data == "connect_chatgpt":
        if (workspaces.get(chat_id) or {}).get("registration"):
            start_chatgpt_login(chat_id)
        return
    try:
        action, draft_id = data.split(":", 1)
    except ValueError:
        return
    draft = state["drafts"].get(draft_id)
    workspace = workspaces.get(chat_id)
    if not workspace or not draft or draft["chat_id"] != chat_id or draft["status"] != "pending":
        return
    if action in ("image", "webimage"):
        try:
            if action == "webimage":
                add_web_image(chat_id, draft, fallback=False)
            else:
                add_draft_image(chat_id, draft)
        except (SetupError, ImageError, WebImageError) as exc:
            send_message(chat_id, str(exc))
        except Exception:
            send_message(chat_id, "Не получилось доставить картинку. Предыдущий черновик сохранён; попробуйте позже.")
        return
    if action == "noimage":
        send_draft(chat_id, draft["text"], replaces=draft)
        return
    if action == "reject":
        draft["status"] = "rejected"
        write_json(STATE_FILE, state)
        tg("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": query["message"]["message_id"], "reply_markup": {"inline_keyboard": []}})
        send_message(chat_id, "Черновик отклонён. Он не будет опубликован.")
    elif action == "approve":
        try:
            start_publish_job(chat_id, draft_id, notify_user=True)
        except SetupError as exc:
            send_message(chat_id, str(exc))
            return
        threading.Thread(
            target=send_message,
            args=(chat_id, "⏳ Одобрение принято. Отправляю пост в канал и сообщу результат."),
            daemon=True,
        ).start()


def start_publish_job(chat_id: str, draft_id: str, notify_user=False):
    with bot_state_lock:
        cutoff = time.time() - 6 * 3600
        for old_job_id, old_job in list(publish_jobs.items()):
            if old_job.get("created_at", 0) < cutoff and old_job.get("status") in ("done", "failed"):
                publish_jobs.pop(old_job_id, None)
        draft = state.get("drafts", {}).get(draft_id)
        workspace = workspaces.get(chat_id)
        if not workspace or not draft or draft.get("chat_id") != chat_id:
            raise SetupError("Черновик не найден.")
        if draft.get("status") != "pending":
            raise SetupError("Этот черновик уже обрабатывается или опубликован.")
        if is_clarification(draft.get("text", "")):
            draft["status"] = "rejected"
            write_json(STATE_FILE, state)
            raise SetupError("В черновике остался запрос уточнений. Подготовьте новый пост.")
        if not draft.get("channel") or draft["channel"] != workspace.get("channel"):
            raise SetupError("Канал для этого черновика изменился. Подготовьте новый пост.")
        draft["status"] = "publishing"
        draft.pop("publish_error", None)
        write_json(STATE_FILE, state)
        job_id = uuid.uuid4().hex
        publish_jobs[job_id] = {
            "chat_id": chat_id, "draft_id": draft_id, "status": "queued",
            "message": "Отправляю пост в канал.", "error": "", "notify_user": bool(notify_user),
            "created_at": time.time(),
        }
    threading.Thread(target=run_publish_job, args=(job_id,), daemon=True).start()
    return job_id


def publish_job_status(chat_id: str, job_id: str):
    job = publish_jobs.get(job_id)
    if not job or job["chat_id"] != chat_id:
        raise SetupError("Задача публикации не найдена.")
    return {key: job[key] for key in ("status", "message", "error")}


def run_publish_job(job_id: str):
    with bot_state_lock:
        job = publish_jobs.get(job_id)
        if not job:
            return
        job["status"] = "running"
        chat_id, draft_id = job["chat_id"], job["draft_id"]
    try:
        publish_draft(chat_id, draft_id)
    except SetupError as exc:
        error = str(exc)
        with bot_state_lock:
            draft = state.get("drafts", {}).get(job["draft_id"])
            if draft:
                draft["publish_error"] = error
                write_json(STATE_FILE, state)
            job.update(status="failed", message="Пост не опубликован.", error=error)
        if job["notify_user"]:
            try:
                send_message(chat_id, f"❌ {error}")
            except Exception:
                pass
        return
    except Exception as exc:
        print(f"Publication failed: {type(exc).__name__}")
        error = "Не удалось подтвердить отправку. Проверьте канал, прежде чем нажимать публикацию повторно."
        with bot_state_lock:
            draft = state.get("drafts", {}).get(job["draft_id"])
            if draft:
                draft["publish_error"] = error
                write_json(STATE_FILE, state)
            job.update(status="failed", message="Нужно проверить канал.", error=error)
        if job["notify_user"]:
            try:
                send_message(chat_id, f"⚠️ {error}")
            except Exception:
                pass
        return
    with bot_state_lock:
        workspace = workspaces.get(chat_id, {})
        label = workspace.get("channel_label", workspace.get("channel", "канал"))
        job.update(status="done", message=f"Пост опубликован в {label}.", error="")
    if job["notify_user"]:
        try:
            send_message(chat_id, f"✅ Пост опубликован в канале {label}.")
        except Exception:
            pass


def publish_draft(chat_id: str, draft_id: str):
    with bot_state_lock:
        workspace = workspaces.get(chat_id)
        draft = state.get("drafts", {}).get(draft_id)
        if not workspace or not draft or draft.get("chat_id") != chat_id or draft.get("status") != "publishing":
            raise SetupError("Статус черновика изменился. Обновите страницу.")
        channel_id = workspace["channel"]
        payload = {"chat_id": channel_id}
        if draft.get("photo_id"):
            payload.update(photo=draft["photo_id"], caption=draft["text"])
            buttons = source_buttons(draft.get("photo_source"))
            if buttons:
                payload["reply_markup"] = {"inline_keyboard": [buttons]}
        else:
            payload.update(text=draft["text"], disable_web_page_preview=True)
        has_photo = bool(draft.get("photo_id"))
    try:
        sent = tg("sendPhoto" if has_photo else "sendMessage", payload)
    except Exception as exc:
        rejected = isinstance(exc, TelegramError) and exc.code is not None and 400 <= exc.code < 500 and exc.code != 408
        with bot_state_lock:
            draft = state["drafts"].get(draft_id)
            if draft and draft.get("status") == "publishing":
                draft["status"] = "pending" if rejected else "publication_unknown"
                write_json(STATE_FILE, state)
        if rejected:
            detail = str(exc)
            if isinstance(exc, TelegramError) and exc.code == 403:
                detail = "боту не хватает права публикации или он удалён из канала"
            raise SetupError(f"Telegram отклонил пост: {detail}. Проверьте права бота и канал.") from None
        raise SetupError("Telegram не подтвердил публикацию. Проверьте канал перед созданием нового поста.") from None
    with bot_state_lock:
        draft = state["drafts"].get(draft_id)
        if not draft:
            raise SetupError("Пост отправлен, но его карточка не найдена.")
        draft["status"] = "published"
        draft["message_id"] = sent["message_id"]
        draft["published_at"] = bot_time().isoformat()
        draft.pop("publish_error", None)
        write_json(STATE_FILE, state)
        try:
            user_database.record_post(chat_id, channel_id, sent["message_id"], draft["published_at"], draft["text"])
        except Exception as exc:
            print(f"Analytics record failed: {type(exc).__name__}")
        preview_message_id = draft.get("preview_message_id")
    if preview_message_id:
        threading.Thread(
            target=clear_draft_buttons,
            args=({"chat_id": chat_id, "preview_message_id": preview_message_id},),
            daemon=True,
        ).start()


@with_bot_state
def scheduled_job():
    now = bot_time()
    now_utc = int(time.time())
    for channel_id in user_database.tracked_channels():
        latest_sample = user_database.latest_member_sample_at(channel_id)
        if latest_sample is not None and now_utc - latest_sample < 15 * 60:
            continue
        try:
            member_count = tg("getChatMemberCount", {"chat_id": channel_id})
            user_database.sample_channel_members(channel_id, member_count, now_utc)
        except (TelegramError, TimeoutError):
            pass
    today = now.date().isoformat()
    for chat_id, workspace in workspaces.items():
        if workspace.get("schedule") != now.strftime("%H:%M") or workspace.get("schedule_date") == today:
            continue
        if not ai_provider(chat_id):
            continue
        with chatgpt_logins_lock:
            if chat_id in chatgpt_logins:
                continue
        workspace["schedule_date"] = today
        write_json(STATE_FILE, state)
        try:
            text = openai_text(chat_id, "Подготовь публикацию на сегодня. Выбери полезную для аудитории тему из профиля.", post=True)
            prepare_post(chat_id, text, "Плановый пост", replaces=pending_for(chat_id))
        except SetupError as exc:
            send_message(chat_id, str(exc))
        except Exception as exc:
            print(f"Scheduled task failed: {exc}")
            send_message(chat_id, "Не удалось выполнить плановую задачу. Проверьте журнал бота.")


def run_bot():
    if check_connection():
        raise SystemExit("Отключите прежнюю webhook-интеграцию перед запуском. Настройки Telegram не изменены.")
    if MINIAPP_PORT:
        from miniapp_server import start_server
        try:
            miniapp = start_server(sys.modules[__name__], int(MINIAPP_PORT))
        except (OSError, ValueError) as exc:
            print(f"Не удалось запустить Mini App: {exc}")
        else:
            print(f"Маркетолог°: http://127.0.0.1:{miniapp.server_port}")
    print(f"Бот запущен. Модель: {CHATGPT_MODEL}; часовой пояс: {TIMEZONE}; расписание: {SCHEDULE_TIME}")
    backoff = 2
    while True:
        try:
            updates = tg("getUpdates", {"offset": int(state.get("offset", 0)) + 1, "timeout": 25, "allowed_updates": ["message", "callback_query", "message_reaction_count"]}) or []
            backoff = 2
            for update in updates:
                with bot_state_lock:
                    if update["update_id"] <= state.get("offset", 0):
                        continue
                    if "message" in update:
                        user_database.observe_discussion(update["message"])
                    elif "message_reaction_count" in update:
                        reaction_update = update["message_reaction_count"]
                        user_database.update_post_reactions(
                            reaction_update["chat"], reaction_update["message_id"], reaction_update["reactions"],
                            reaction_update.get("date"),
                        )
                    previous_offset = state.get("offset", 0)
                    state["offset"] = update["update_id"]
                    try:
                        write_json(STATE_FILE, state)
                    except OSError:
                        state["offset"] = previous_offset
                        raise
                    if "message" in update:
                        handle_message(update["message"])
                    elif "callback_query" in update:
                        try:
                            handle_callback(update["callback_query"])
                        except Exception as exc:
                            print(f"Callback handling failed: {exc}")
            scheduled_job()
        except KeyboardInterrupt:
            print("Бот остановлен.")
            return
        except Exception as exc:
            if isinstance(exc, TelegramError) and exc.code == 409:
                raise SystemExit(conflict_message(exc))
            print(f"Polling error: {exc}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    bot_id, separator, _ = BOT_TOKEN.partition(":")
    if not separator or not bot_id.isdecimal():
        raise SystemExit("Укажите токен Telegram-бота в bot/.env.")
    if "--check" in sys.argv[1:]:
        try:
            check_connection()
        except TelegramError as exc:
            raise SystemExit(f"Проверка Telegram не удалась (HTTP {exc.code or 'недоступен'}). Проверьте токен и соединение.") from None
        return
    lock_path = Path(tempfile.gettempdir()) / f"ai-marketolog-{bot_id}.lock"
    with lock_path.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit("Этот бот уже запущен. Остановите предыдущий процесс через Ctrl+C.")
        recover_pending_state()
        recover_abandoned_publications()
        migrate_profiles()
        run_bot()


if __name__ == "__main__":
    main()
