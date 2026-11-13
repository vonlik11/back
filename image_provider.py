import base64
import binascii
import hashlib
import json
import os
import urllib.error
import urllib.request


class ImageError(Exception):
    pass


def configured():
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def generate_image(prompt, user_id):
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise ImageError("Генерация картинок ещё не подключена. Владельцу бота нужно добавить OPENAI_API_KEY в .env и перезапустить бота. Подключение ChatGPT для текста продолжает работать.")
    payload = {
        "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5"),
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
