import base64
import binascii
import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


class ImageError(Exception):
    pass


def configured(user_id=None):
    if os.getenv("OPENAI_API_KEY", "").strip():
        return True
    if user_id is None:
        return False
    directory = Path(__file__).resolve().parent / ".auth" / "telegram" / str(user_id)
    try:
        return (directory / "provider").read_text(encoding="utf-8").strip() == "api" and (directory / "api-key.enc").is_file()
    except OSError:
        return False


def generate_with_user_key(prompt, user_id):
    result = subprocess.run(
        ["node", "siwc.mjs", "image-api", str(user_id)],
        cwd=Path(__file__).resolve().parent,
        input=json.dumps({"prompt": prompt, "safetyIdentifier": hashlib.sha256(f"ai-marketolog:{user_id}".encode()).hexdigest()}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=190,
    )
    if result.returncode:
        code = result.stderr.strip()
        messages = {
            "image_http_401": "OpenAI отклонил API-ключ. Подключите его заново через /api.",
            "image_http_403": "У API-проекта нет доступа к модели изображений. Проверьте доступ и верификацию организации.",
            "image_http_402": "На API-проекте нет доступного баланса. Пополните баланс или выберите фото из интернета.",
            "image_http_429": "Лимит генерации изображений исчерпан. Попробуйте позже или выберите фото из интернета.",
        }
        raise ImageError(messages.get(code, "OpenAI не смог создать изображение. Проверьте доступ к модели, баланс API и повторите позже."))
    try:
        image = base64.b64decode(json.loads(result.stdout)["image"], validate=True)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, binascii.Error):
        raise ImageError("OpenAI вернул изображение в неподдерживаемом формате.") from None
    if not image.startswith(b"\xff\xd8\xff") or len(image) > 10 * 1024 * 1024:
        raise ImageError("Изображение имеет неподходящий формат или размер для Telegram.")
    return image


def generate_image(prompt, user_id):
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        if configured(user_id):
            try:
                return generate_with_user_key(prompt, user_id)
            except subprocess.TimeoutExpired:
                raise ImageError("Генерация изображения превысила лимит времени. Черновик сохранён; попробуйте ещё раз или выберите фото из интернета.") from None
        raise ImageError("Для генерации изображения подключите OpenAI API через /api. Поиск лицензированных фото работает без API-ключа.")
    payload = {
        "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare"),
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "quality": "medium",
        "output_format": "jpeg",
        "safety_identifier": hashlib.sha256(f"ai-marketolog:{user_id}".encode()).hexdigest(),
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        messages = {
            401: "Ключ сервиса изображений недействителен. Владельцу нужно проверить OPENAI_API_KEY.",
            403: "У API-аккаунта нет доступа к выбранной модели изображений. Проверьте доступ и верификацию организации.",
            429: "Лимит сервиса изображений исчерпан. Проверьте баланс API или попробуйте позже.",
        }
        raise ImageError(messages.get(exc.code, f"Сервис изображений отклонил запрос (HTTP {exc.code}). Попробуйте изменить описание или повторить позже.")) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ImageError("Сервис изображений не ответил вовремя. Черновик сохранён; повторите генерацию позже.") from None
    except (ValueError, UnicodeError):
        raise ImageError("Сервис изображений вернул некорректный ответ.") from None
    try:
        image = base64.b64decode(result["data"][0]["b64_json"], validate=True)
    except (KeyError, IndexError, TypeError, ValueError, binascii.Error):
        raise ImageError("Сервис не вернул изображение. Попробуйте изменить описание.") from None
    if not image.startswith(b"\xff\xd8\xff") or len(image) > 10 * 1024 * 1024:
        raise ImageError("Изображение имеет неподходящий формат или размер для Telegram.")
    return image
