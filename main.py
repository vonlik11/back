"""Small, dependency-free Telegram AI marketer MVP."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
PROFILE_FILE = ROOT / "company.json"


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


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


state = read_json(STATE_FILE, {"offset": 0, "chats": {}, "drafts": {}, "schedule_date": ""})
profile = read_json(PROFILE_FILE, {
    "name": "Листва Coffee",
    "description": "Кофейня в центре Екатеринбурга. Спешелти кофе и собственная обжарка, уютные завтраки.",
    "address": "Екатеринбург, ул. Малышева, 24",
    "hours": "ежедневно, 08:00–21:00",
    "tone": "дружелюбный, тёплый, современный; короткие живые фразы без громких обещаний",
    "rules": "Не придумывай цены, скидки, состав блюд или факты, которых нет в профиле. Пиши по-русски."
})


class TelegramError(Exception):
    pass


def http_json(url: str, payload: dict | None = None, headers: dict | None = None, timeout: int = 40):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise TelegramError(f"HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise TelegramError(str(exc.reason)) from exc


def tg(method: str, payload: dict | None = None):
    if not BOT_TOKEN:
        raise TelegramError("В .env не задан TELEGRAM_BOT_TOKEN")
    result = http_json(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", payload, {"Content-Type": "application/json"}, timeout=35)
    if not result.get("ok"):
        raise TelegramError(result.get("description", "Ошибка Telegram Bot API"))
    return result.get("result")


def send_message(chat_id, text: str, markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if markup:
        payload["reply_markup"] = markup
    return tg("sendMessage", payload)


def openai_text(chat_id: str, user_text: str, extra: str = "") -> str:
    history = state["chats"].setdefault(str(chat_id), [])[-8:]
    transcript = "\n".join(f"{item['role']}: {item['text']}" for item in history)
    instructions = (
        "Ты AI-маркетолог компании. Отвечай по-русски, естественно и по делу. "
        "Если пользователь просит подготовить публикацию, верни только готовый текст поста, "
        "без вводной фразы и без пояснений. Не добавляй неподтверждённые факты. "
        "При неоднозначном запросе задай короткий уточняющий вопрос.\n\n"
        f"ПРОФИЛЬ КОМПАНИИ:\n{json.dumps(profile, ensure_ascii=False)}\n\n"
        f"ПРЕДЫДУЩИЙ ДИАЛОГ:\n{transcript or 'пока нет'}"
    )
    prompt = (extra + "\n" if extra else "") + user_text
    request = subprocess.run(
        ["node", "siwc.mjs", "infer"], cwd=ROOT,
        input=json.dumps({"prompt": prompt, "instructions": instructions}, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if request.returncode:
        detail = request.stderr.strip() or "Неизвестная ошибка локального подключения ChatGPT"
        raise RuntimeError(detail[:500])
    answer = json.loads(request.stdout).get("text", "").strip()
    if not answer:
        raise RuntimeError("Модель вернула пустой ответ")
    history.extend([{"role": "user", "text": user_text}, {"role": "assistant", "text": answer}])
    state["chats"][str(chat_id)] = history[-16:]
    write_json(STATE_FILE, state)
    return answer


def pending_for(chat_id: str):
    return next((draft for draft in reversed(list(state["drafts"].values()))
                 if draft["chat_id"] == str(chat_id) and draft["status"] == "pending"), None)


def send_draft(chat_id: str, text: str, draft_id: str | None = None):
    if not draft_id:
        draft_id = str(int(time.time() * 1000))
    state["drafts"][draft_id] = {"chat_id": str(chat_id), "text": text, "status": "pending"}
    write_json(STATE_FILE, state)
    markup = {"inline_keyboard": [[
        {"text": "✅ Одобрить и опубликовать", "callback_data": f"approve:{draft_id}"},
        {"text": "↩️ Отклонить", "callback_data": f"reject:{draft_id}"},
    ]]}
    send_message(chat_id, "📝 Черновик на согласование\n\n" + text + "\n\nПост выйдет в канал только после вашего одобрения. Чтобы изменить текст, напишите: «переделай: …».", markup)


def is_admin(chat_id: str) -> bool:
    return bool(ADMIN_CHAT_ID) and str(chat_id) == str(ADMIN_CHAT_ID)


def handle_message(message: dict):
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return
    if text.startswith("/start") or text.startswith("/whoami"):
        if not ADMIN_CHAT_ID:
            send_message(chat_id, f"Ваш Telegram chat ID: {chat_id}\n\nДля безопасности добавьте его в bot/.env как TELEGRAM_ADMIN_CHAT_ID и перезапустите бота. Это ограничит управление только вашим аккаунтом.")
        elif is_admin(chat_id):
            send_message(chat_id, f"Привет! Я AI-маркетолог компании «{profile.get('name', 'Компания')}». Напишите, что подготовить. Важные действия сначала отправляю на согласование.")
        else:
            send_message(chat_id, "Доступ к этому боту закрыт.")
        return
    if not is_admin(chat_id):
        send_message(chat_id, "Доступ закрыт. Напишите владельцу бота, чтобы настроить разрешённый Telegram аккаунт.")
        return
    if text.startswith("/help"):
        send_message(chat_id, "/start — начать\n/status — статус черновика\nЛюбая задача — ответить или подготовить контент\n«переделай: …» — изменить последний черновик")
        return
    if text.startswith("/connect"):
        send_message(chat_id, "Чтобы подключить ваш ChatGPT-план, остановите бота на компьютере и в папке bot выполните `npm run login`. В браузере войдите в ChatGPT и явно разрешите AI-маркетологу использовать лимиты плана, затем снова запустите бота.")
        return
    if text.startswith("/status"):
        draft = pending_for(chat_id)
        send_message(chat_id, "Есть черновик, ожидающий согласования." if draft else "Нет черновиков на согласовании.")
        return
    try:
        existing = pending_for(chat_id)
        if existing and (text.lower().startswith("переделай") or text.lower().startswith("измени")):
            instruction = text.split(":", 1)[-1].strip()
            revised = openai_text(chat_id, "Внеси правки в мой предыдущий черновик по инструкции. Верни только новую версию поста.", f"ТЕКУЩИЙ ЧЕРНОВИК:\n{existing['text']}\n\nИНСТРУКЦИЯ:\n{instruction}")
            send_draft(chat_id, revised)
        else:
            answer = openai_text(chat_id, text)
            if len(answer) > 90 or any(word in text.lower() for word in ("пост", "публикац", "контент", "напиши", "подготовь", "сделай текст")):
                send_draft(chat_id, answer)
            else:
                send_message(chat_id, answer)
    except Exception as exc:
        print(f"Message handling failed: {exc}")
        send_message(chat_id, "Не получилось обработать запрос. Проверьте настройки API и попробуйте ещё раз.")


def handle_callback(query: dict):
    data = query.get("data", "")
    chat_id = str(query.get("message", {}).get("chat", {}).get("id", ""))
    tg("answerCallbackQuery", {"callback_query_id": query["id"]})
    try:
        action, draft_id = data.split(":", 1)
    except ValueError:
        return
    draft = state["drafts"].get(draft_id)
    if not is_admin(chat_id) or not draft or draft["status"] != "pending":
        return
    if action == "reject":
        draft["status"] = "rejected"
        write_json(STATE_FILE, state)
        tg("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": query["message"]["message_id"], "reply_markup": {"inline_keyboard": []}})
        send_message(chat_id, "Черновик отклонён. Он не будет опубликован.")
    elif action == "approve":
        if not CHANNEL_ID:
            send_message(chat_id, "В bot/.env не настроен TELEGRAM_CHANNEL_ID. Публикация не выполнена.")
            return
        draft["status"] = "publishing"
        write_json(STATE_FILE, state)
        try:
            sent = tg("sendMessage", {"chat_id": CHANNEL_ID, "text": draft["text"], "disable_web_page_preview": True})
        except Exception as exc:
            draft["status"] = "pending"
            write_json(STATE_FILE, state)
            print(f"Channel publish failed: {exc}")
            send_message(chat_id, "Telegram не принял публикацию. Проверьте, что бот добавлен администратором канала с правом размещать сообщения. Черновик сохранён, можно попробовать ещё раз.")
            return
        draft["status"] = "published"
        draft["message_id"] = sent.get("message_id")
        write_json(STATE_FILE, state)
        tg("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": query["message"]["message_id"], "reply_markup": {"inline_keyboard": []}})
        send_message(chat_id, f"✅ Опубликовано в канале {CHANNEL_ID}.")


def scheduled_job():
    if not ADMIN_CHAT_ID:
        return
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        now = datetime.now(timezone(timedelta(hours=5))) if TIMEZONE == "Asia/Yekaterinburg" else datetime.now().astimezone()
    today = now.strftime("%Y-%m-%d")
    if now.strftime("%H:%M") != SCHEDULE_TIME or state.get("schedule_date") == today:
        return
    state["schedule_date"] = today
    write_json(STATE_FILE, state)
    try:
        text = openai_text(ADMIN_CHAT_ID, "Подготовь идею и готовую публикацию для соцсетей компании на сегодня. Выбери полезную для аудитории тему из профиля. Не придумывай факты.")
        send_draft(ADMIN_CHAT_ID, text)
        print(f"Scheduled draft prepared at {now.isoformat()}")
    except Exception as exc:
        print(f"Scheduled task failed: {exc}")
        send_message(ADMIN_CHAT_ID, "Не удалось выполнить плановую задачу. Проверьте настройки и журнал бота.")


def main():
    if not BOT_TOKEN:
        raise SystemExit("Сначала заполните bot/.env. См. bot/README.md")
    status = subprocess.run(["node", "siwc.mjs", "status"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    connected = False
    if status.returncode == 0:
        try: connected = json.loads(status.stdout).get("connected", False)
        except json.JSONDecodeError: pass
    print(f"Бот запущен. Модель: {CHATGPT_MODEL}; часовой пояс: {TIMEZONE}; расписание: {SCHEDULE_TIME}")
    if not connected:
        print("ChatGPT ещё не подключён. Выполните `npm run login` в папке bot.")
    backoff = 2
    while True:
        try:
            updates = tg("getUpdates", {"offset": int(state.get("offset", 0)) + 1, "timeout": 25, "allowed_updates": ["message", "callback_query"]}) or []
            backoff = 2
            for update in updates:
                state["offset"] = update["update_id"]
                write_json(STATE_FILE, state)
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
            print(f"Polling error: {exc}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    main()
