"""Генерация иллюстраций через RouterAI / Recraft и локальная обложка."""
from __future__ import annotations

import base64
import io
import binascii
import hashlib
import json
import math
import os
import struct
import subprocess
import shutil
import urllib.error
import urllib.parse
import urllib.request
import zlib
import time
import uuid
from pathlib import Path
import api_credentials

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE_API_KEY = ""


class ImageError(Exception):
    pass


DEFAULT_IMAGE_MODEL = "black-forest-labs/flux-3-image"


def get_configured_api_key() -> str:
    return os.getenv("ROUTERAI_IMAGE_API_KEY", "").strip() or os.getenv("ROUTERAI_API_KEY", "").strip()


def image_model() -> str:
    return os.getenv("ROUTERAI_IMAGE_MODEL", "").strip() or DEFAULT_IMAGE_MODEL


def configured(user_id=None) -> bool:
    provider = api_credentials.provider_config(user_id).get("provider") if user_id and api_credentials.has_api_key(user_id) else None
    return bool(get_configured_api_key()) or provider in ("openai", "routerai")


def _valid_image_bytes(image: bytes) -> bool:
    return (
        bool(image)
        and len(image) <= 10 * 1024 * 1024
        and (image.startswith(b"\xff\xd8\xff") or image.startswith(b"\x89PNG\r\n\x1a\n"))
    )


def telegram_image(image: bytes) -> bytes:
    """Recraft returns WebP; Telegram sendPhoto and our media store expect PNG/JPEG."""
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(image)) as source:
                if source.width * source.height > 25_000_000:
                    raise ImageError("Изображение превышает допустимое разрешение.")
                output = io.BytesIO()
                source.convert("RGBA" if "A" in source.getbands() else "RGB").save(output, format="PNG")
                image = output.getvalue()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            raise ImageError("Не удалось обработать WebP-изображение от RouterAI.") from None
    if not _valid_image_bytes(image):
        raise ImageError("Изображение имеет неподходящий формат или размер для Telegram.")
    return image


def generate_with_user_key(prompt: str, user_id) -> bytes:
    # Compatibility entry point: images now use the server's shared RouterAI key.
    return generate_image(prompt, user_id)


def _extract_image_from_response(result: dict) -> bytes:
    item = (result.get("data") or [{}])[0]
    if "b64_json" in item and item["b64_json"]:
        return base64.b64decode(item["b64_json"], validate=True)
    if "url" in item and item["url"]:
        req = urllib.request.Request(item["url"], headers={"User-Agent": "MarkAI/1.0"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.read(10 * 1024 * 1024 + 1)
    raise KeyError("No image payload")


def generate_image(prompt: str, user_id) -> bytes:
    from ai.progress import report
    config = api_credentials.provider_config(user_id) if user_id and api_credentials.has_api_key(user_id) else {}
    personal_key = api_credentials.get_api_key(user_id) if config else ""
    if personal_key and config.get("provider") == "openai":
        report("image_connect", "Подключаюсь к вашему OpenAI API…")
        try:
            result = api_credentials.run_helper("image-api", user_id, {
                "prompt": prompt,
                "safetyIdentifier": hashlib.sha256(str(user_id).encode("utf-8")).hexdigest(),
            }, 190)
            image = base64.b64decode(result.get("image", ""), validate=True)
        except (RuntimeError, ValueError, binascii.Error) as exc:
            reason = str(exc)
            if reason.startswith("image_http_401"):
                raise ImageError("OpenAI API отклонил ключ. Переподключите его через /api.") from None
            if reason.startswith("image_http_402") or reason.startswith("image_http_429"):
                raise ImageError("OpenAI API ограничил генерацию. Проверьте баланс и лимиты проекта.") from None
            if reason.startswith("image_http_403") or reason.startswith("image_http_404"):
                raise ImageError("У ключа нет доступа к модели изображений. Проверьте настройки API-проекта.") from None
            raise ImageError("Не удалось создать изображение через ваш OpenAI API. Проверьте доступ к модели и попробуйте позже.") from None
        report("image_download", "Картинка готова — обрабатываю результат…")
        return telegram_image(image)
    if personal_key and config.get("provider") not in ("routerai", "openai") and not get_configured_api_key():
        raise ImageError("Текстовый API подключён. Генерация изображений поддерживается для OpenAI и RouterAI; задайте общий ключ изображений или подключите один из них.")
    key = personal_key if config.get("provider") == "routerai" else get_configured_api_key()
    if not key:
        raise ImageError("Подключите личный OpenAI API командой /api или попросите администратора настроить общий ключ RouterAI.")
    aspect_ratio = os.getenv("ROUTERAI_IMAGE_ASPECT_RATIO", "1:1").strip()
    if aspect_ratio not in ("1:1", "4:3", "3:4", "16:9", "9:16", "auto"):
        raise ImageError("Неподдерживаемое соотношение сторон ROUTERAI_IMAGE_ASPECT_RATIO.")
    report("image_connect", "Подключаюсь к Recraft через RouterAI…")
    payload = {"model": image_model(), "prompt": prompt, "n": 1, "aspect_ratio": aspect_ratio}
    request = urllib.request.Request(
        "https://routerai.ru/api/v1/images",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST",
    )
    try:
        report("image", "Recraft генерирует картинку…")
        # No retries: retrying a paid generation can create duplicate charges.
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ImageError("RouterAI вернул слишком большое изображение.")
            result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        messages = {
            401: "RouterAI: ключ изображений недействителен. Обратитесь к администратору.",
            402: "RouterAI: недостаточно средств для картинки. Пополните баланс.",
            403: "RouterAI: нет доступа к модели изображений.",
            404: "RouterAI: модель изображений или эндпоинт недоступны.",
            429: "RouterAI: лимит запросов изображений. Повторите позже.",
        }
        raise ImageError(messages.get(exc.code, f"RouterAI отклонил генерацию картинки (HTTP {exc.code}).")) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ImageError("RouterAI не ответил вовремя. Проверьте историю генераций в RouterAI перед повтором: запрос мог быть оплачен.") from None
    except (ValueError, UnicodeError):
        raise ImageError("RouterAI вернул некорректный ответ изображения.") from None
    report("image_download", "Картинка готова — обрабатываю результат…")
    try:
        encoded = result["data"][0]["b64_json"]
        image = base64.b64decode(encoded, validate=True)
    except (KeyError, IndexError, TypeError, ValueError, binascii.Error):
        raise ImageError("RouterAI не вернул изображение в base64.") from None
    return telegram_image(image)


def render_brand_cover_png(seed_text: str, width: int = 800, height: int = 500) -> bytes:
    """Генерирует стильную градиентную PNG-обложку для поста (без внешних зависимостей), если внешний API недоступен."""
    digest = hashlib.sha256((seed_text or "mark-ai").encode("utf-8")).digest()
    palettes = [
        ((15, 23, 42), (30, 64, 175), (56, 189, 248)),
        ((24, 24, 27), (67, 56, 202), (168, 85, 247)),
        ((6, 78, 59), (15, 118, 110), (52, 211, 153)),
        ((31, 41, 55), (180, 83, 9), (251, 191, 36)),
        ((17, 24, 39), (157, 23, 77), (244, 114, 182)),
    ]
    c1, c2, c3 = palettes[digest[0] % len(palettes)]
    cx1 = int(width * (0.25 + (digest[1] / 255.0) * 0.25))
    cy1 = int(height * (0.25 + (digest[2] / 255.0) * 0.25))
    cx2 = int(width * (0.60 + (digest[3] / 255.0) * 0.25))
    cy2 = int(height * (0.55 + (digest[4] / 255.0) * 0.25))
    r1 = max(width, height) * 0.48
    r2 = max(width, height) * 0.42

    rows = []
    for y in range(height):
        ty = y / max(1, height - 1)
        row = bytearray([0])  # filter type 0
        for x in range(width):
            tx = x / max(1, width - 1)
            t = 0.55 * tx + 0.45 * ty
            r = c1[0] + (c2[0] - c1[0]) * t
            g = c1[1] + (c2[1] - c1[1]) * t
            b = c1[2] + (c2[2] - c1[2]) * t

            d1 = math.hypot(x - cx1, y - cy1)
            if d1 < r1:
                w1 = (1.0 - d1 / r1) ** 2 * 0.55
                r = r * (1.0 - w1) + c3[0] * w1
                g = g * (1.0 - w1) + c3[1] * w1
                b = b * (1.0 - w1) + c3[2] * w1

            d2 = math.hypot(x - cx2, y - cy2)
            if d2 < r2:
                w2 = (1.0 - d2 / r2) ** 2 * 0.4
                r = r * (1.0 - w2) + 255 * w2
                g = g * (1.0 - w2) + 255 * w2
                b = b * (1.0 - w2) + 255 * w2

            # subtle glass card frame in the center
            if int(width * 0.12) <= x <= int(width * 0.88) and int(height * 0.16) <= y <= int(height * 0.84):
                border = (
                    x in (int(width * 0.12), int(width * 0.88))
                    or y in (int(height * 0.16), int(height * 0.84))
                )
                if border:
                    r = min(255, r * 0.65 + 90)
                    g = min(255, g * 0.65 + 95)
                    b = min(255, b * 0.65 + 105)
                else:
                    r = min(255, r * 0.88 + 22)
                    g = min(255, g * 0.88 + 24)
                    b = min(255, b * 0.88 + 30)

            row.extend((max(0, min(255, int(r))), max(0, min(255, int(g))), max(0, min(255, int(b)))))
        rows.append(bytes(row))

    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw, level=6)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
