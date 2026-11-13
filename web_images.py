import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request


class WebImageError(Exception):
    pass


HEADERS = {"User-Agent": "AI-Marketolog/0.1 (Telegram editorial image search)"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_LICENSES = {"cc0", "public-domain"}


def plain_text(value):
    return html.unescape(re.sub(r"<[^>]*>", " ", str(value))).strip()


def safe_image_url(url):
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in ("upload.wikimedia.org", "thumb.wikimedia.org") and not parsed.username and not parsed.password and parsed.port in (None, 443)


class ImageRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_image_url(newurl):
            raise WebImageError("Источник перенаправил запрос на неизвестный адрес.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def search_images(query):
    query = " ".join(re.findall(r"[\w-]+", query, re.UNICODE)[:10])[:160]
    if not query:
        return []
    params = {
        "action": "query", "format": "json", "generator": "search", "gsrsearch": query + " filetype:bitmap",
        "gsrnamespace": 6, "gsrlimit": 20, "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata", "iiurlwidth": 1200,
    }
    request = urllib.request.Request("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params), headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise WebImageError("Поиск изображений временно недоступен.") from None
    if not isinstance(data, dict) or "error" in data:
        raise WebImageError("Сервис поиска не смог обработать запрос.")
    results = []
    for page in sorted(data.get("query", {}).get("pages", {}).values(), key=lambda item: item.get("index", 999)):
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        meta = info.get("extmetadata", {})
        license_name = plain_text(meta.get("LicenseShortName", {}).get("value", ""))
        url = info.get("thumburl") or info.get("url", "")
        normalized_license = re.sub(r"[^a-z0-9]+", "-", license_name.casefold()).strip("-")
        reusable_license = normalized_license in ALLOWED_LICENSES or bool(re.fullmatch(r"cc0(?:-[0-9]+(?:-[0-9]+)?)?", normalized_license)) or bool(re.fullmatch(r"cc-by(?:-sa)?(?:-[0-9]+(?:-[0-9]+)?)?", normalized_license))
        if not reusable_license or not safe_image_url(url):
            continue
        if info.get("mime") not in ("image/jpeg", "image/png") or min(info.get("width", 0), info.get("height", 0)) < 400:
            continue
        results.append({
            "kind": "web", "url": f"https://commons.wikimedia.org/?curid={int(page['pageid'])}",
            "download_url": url, "title": plain_text(page.get("title", ""))[:200],
            "description": plain_text(meta.get("ImageDescription", {}).get("value", ""))[:700],
            "author": plain_text(meta.get("Artist", {}).get("value", ""))[:200], "license": license_name,
            "license_url": plain_text(meta.get("LicenseUrl", {}).get("value", ""))[:500],
        })
    return results[:6]


def download_image(url):
    if not safe_image_url(url):
        raise WebImageError("Недопустимый адрес изображения.")
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.build_opener(ImageRedirectHandler()).open(request, timeout=12) as response:
            image = response.read(MAX_IMAGE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise WebImageError("Не удалось загрузить найденное изображение.") from None
    if len(image) > MAX_IMAGE_BYTES or not (image.startswith(b"\xff\xd8\xff") or image.startswith(b"\x89PNG\r\n\x1a\n")):
        raise WebImageError("Найденный файл не подходит для публикации в Telegram.")
    return image
