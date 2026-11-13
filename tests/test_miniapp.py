import hashlib
import hmac
import io
import json
import sys
import tempfile
import time
import unittest
import urllib.parse
from email.message import Message
from pathlib import Path
from types import MethodType
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as bot
import miniapp_server
from miniapp_server import start_server
from user_database import UserDatabase


class MiniAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-miniapp-", dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.state = {"offset": 0, "chats": {}, "drafts": {}, "workspaces": {}}
        self.workspace = {
            "channel": "-100123", "channel_label": "@factory",
            "profile": {"name": "Factory", "description": "Glass manufacturing"},
            "registration": {"display_name": "Timur", "telegram_user_id": "1", "email": "user@example.com", "registered_at": "2026-11-13"},
        }
        self.state["workspaces"]["1"] = self.workspace
        self.database = UserDatabase(Path(self.temp.name) / "users.sqlite3")
        credential = bot.password_record("a-secure-password")
        self.database.save("1", "user@example.com", credential, display_name="Timur", company_name="Factory", company_description="Glass manufacturing", channel_id="-100123", channel_label="@factory", registered_at="2026-11-13")
        self.calls = []
        self.telegram_id = 100

        def telegram(method, payload=None):
            self.telegram_id += 1
            self.calls.append((method, payload or {}))
            if method == "sendPhoto":
                return {"message_id": self.telegram_id, "photo": [{"file_id": "uploaded-photo"}]}
            return {"message_id": self.telegram_id}

        self.patches = [
            patch.object(bot, "state", self.state),
            patch.object(bot, "workspaces", self.state["workspaces"]),
            patch.object(bot, "STATE_FILE", Path(self.temp.name) / "state.json"),
            patch.object(bot, "user_database", self.database),
            patch.object(bot, "publish_jobs", {}),
            patch.object(bot, "BOT_USERNAME", "test_mark_bot"),
            patch.object(bot, "BOT_TOKEN", "123456:test-token"),
            patch.object(bot, "ai_provider", return_value="chatgpt"),
            patch.object(bot, "tg", side_effect=telegram),
            patch.object(bot, "openai_text", return_value="Готовый пост о стекле."),
        ]
        for active in self.patches:
            active.start()
            self.addCleanup(active.stop)
        class LocalServer:
            def __init__(self, address, handler):
                self.RequestHandlerClass = handler
                self.server_port = 3000

            def serve_forever(self):
                return None

            def shutdown(self):
                return None

            def server_close(self):
                return None

        server_patch = patch.object(miniapp_server, "ThreadingHTTPServer", LocalServer)
        server_patch.start()
        self.addCleanup(server_patch.stop)
        self.server = start_server(bot, 0)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port
        self.cookie = ""

    def request(self, path, data=None, method=None):
        body = None if data is None else json.dumps(data).encode()
        handler = self.server.RequestHandlerClass.__new__(self.server.RequestHandlerClass)
        handler.path = path
        handler.headers = Message()
        handler.headers["X-Mark-App"] = "1"
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Content-Length"] = str(len(body or b""))
        if self.cookie:
            handler.headers["Cookie"] = self.cookie
        handler.rfile = io.BytesIO(body or b"")
        handler.wfile = io.BytesIO()
        handler.client_address = ("127.0.0.1", 12345)
        handler.server = self.server
        response = {}

        def reply(instance, status, response_body, media_type, cookie=None):
            response.update(status=status, headers={"Content-Type": media_type}, body=response_body, cookie=cookie)

        def json_reply(instance, status, payload, cookie=None):
            instance.reply(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8", cookie)

        handler.reply = MethodType(reply, handler)
        handler.json_reply = MethodType(json_reply, handler)
        if body is None:
            handler.do_GET()
        else:
            handler.do_POST()
        if response.get("cookie"):
            self.cookie = response["cookie"].split(";", 1)[0]
        return response["status"], response["headers"], response["body"]

    def post(self, path, data):
        status, _, body = self.request(path, data)
        return status, json.loads(body)

    def login(self):
        status, _, body = self.request("/api/login", {"email": "user@example.com", "password": "a-secure-password"})
        self.assertEqual(status, 200)
        return json.loads(body)

    def create_text_post(self):
        status, result = self.post("/api/posts", {"topic": "О стекле", "illustrate": False, "photo": None})
        self.assertEqual(status, 200)
        return result["posts"][0]["id"]

    def wait_job(self, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, _, body = self.request(f"/api/jobs/{job_id}")
            self.assertEqual(status, 200)
            result = json.loads(body)
            if result["status"] in ("done", "failed"):
                return result
            time.sleep(0.01)
        self.fail("The Mini App job did not finish")

    def test_static_config_auth_and_logout_buttons(self):
        for path, marker in (("/", "Маркетолог"), ("/app.js", "data-action"), ("/styles.css", ".device")):
            status, _, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(marker.encode(), body)
        status, _, body = self.request("/api/config")
        self.assertEqual(json.loads(body)["bot"], "test_mark_bot")
        status, _, body = self.request("/api/dashboard")
        self.assertEqual(status, 401)
        dashboard = self.login()
        self.assertEqual(dashboard["company"], "Factory")
        status, _, body = self.request("/api/dashboard")
        self.assertEqual(status, 200)
        self.assertIn("analytics", json.loads(body))
        status, _, body = self.request("/api/logout", {}, "POST")
        self.assertEqual(status, 200)
        status, _, _ = self.request("/api/dashboard")
        self.assertEqual(status, 401)

    def test_telegram_login_button_verifies_signed_init_data(self):
        auth_date = str(int(time.time()))
        user = json.dumps({"id": 1, "first_name": "Timur"}, separators=(",", ":"))
        fields = {"auth_date": auth_date, "user": user}
        check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.digest(b"WebAppData", bot.BOT_TOKEN.encode(), "sha256")
        fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        init_data = urllib.parse.urlencode(fields)
        status, result = self.post("/api/login", {"init_data": init_data})
        self.assertEqual(status, 200)
        self.assertEqual(result["email"], "user@example.com")

    def test_api_connect_schedule_and_profile_buttons(self):
        self.login()
        with patch.object(bot, "save_openai_key") as connect:
            status, _ = self.post("/api/connect", {"key": "sk-proj-example"})
        self.assertEqual(status, 200)
        connect.assert_called_once_with("1", "sk-proj-example")
        status, result = self.post("/api/schedule", {"time": "09:30"})
        self.assertEqual(status, 200)
        self.assertEqual(result["schedule"], "09:30")
        status, result = self.post("/api/schedule", {"time": None})
        self.assertEqual(status, 200)
        self.assertIsNone(result["schedule"])
        status, result = self.post("/api/profile", {"company": "Glass Co", "description": "Modern glass"})
        self.assertEqual(status, 200)
        self.assertEqual(result["company"], "Glass Co")
        self.assertEqual(self.database.get("1")["company_description"], "Modern glass")

    def test_create_edit_reject_and_publish_controls(self):
        self.login()
        draft_id = self.create_text_post()
        status, result = self.post(f"/api/posts/{draft_id}/edit", {"text": "Изменённый пост."})
        self.assertEqual(status, 200)
        new_id = result["posts"][0]["id"]
        self.assertNotEqual(draft_id, new_id)
        self.assertEqual(result["posts"][0]["text"], "Изменённый пост.")
        status, result = self.post(f"/api/posts/{new_id}/reject", {})
        self.assertEqual(status, 200)
        self.assertEqual(result["posts"][0]["status"], "rejected")
        publish_id = self.create_text_post()
        status, result = self.post(f"/api/posts/{publish_id}/publish", {})
        self.assertEqual(status, 200)
        job = self.wait_job(result["job_id"])
        self.assertEqual(job["status"], "done")
        self.assertTrue(any(method == "sendMessage" and data.get("chat_id") == "-100123" for method, data in self.calls))
        self.assertEqual(self.state["drafts"][publish_id]["status"], "published")

    def test_image_search_text_only_and_photo_delivery_controls(self):
        self.login()
        draft_id = self.create_text_post()
        with patch.object(bot, "add_draft_image") as generate:
            status, _ = self.post(f"/api/posts/{draft_id}/image", {})
        self.assertEqual(status, 200)
        generate.assert_called_once_with("1", self.state["drafts"][draft_id])
        with patch.object(bot, "add_web_image") as search:
            status, _ = self.post(f"/api/posts/{draft_id}/webimage", {})
        self.assertEqual(status, 200)
        search.assert_called_once_with("1", self.state["drafts"][draft_id], fallback=False)
        status, result = self.post(f"/api/posts/{draft_id}/noimage", {})
        self.assertEqual(status, 200)
        self.assertEqual(result["posts"][0]["status"], "pending")

        photo_id = "a" * 32
        self.state["drafts"][photo_id] = {
            "chat_id": "1", "channel": "-100123", "text": "Фото поста", "photo_id": "file-id",
            "photo_source": {"kind": "user"}, "preview_message_id": 20, "status": "pending",
        }
        with patch.object(bot, "download_user_image", return_value=b"\xff\xd8\xffimage"):
            status, headers, body = self.request(f"/api/posts/{photo_id}/photo")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(body, b"\xff\xd8\xffimage")

    def test_invalid_actions_and_unauthenticated_requests_are_rejected(self):
        status, _ = self.post("/api/login", {"email": "wrong@example.com", "password": "not-a-password"})
        self.assertEqual(status, 401)
        self.login()
        status, _ = self.post("/api/schedule", {"time": "25:90"})
        self.assertEqual(status, 400)
        status, _ = self.post("/api/posts/not-a-draft/publish", {})
        self.assertEqual(status, 404)

    def test_visible_site_actions_and_forms_are_connected_to_handlers(self):
        source = (Path(__file__).resolve().parents[1] / "miniapp" / "app.js").read_text(encoding="utf-8")
        for action in ("close-sheet", "telegram-login", "refresh-analytics", "tab", "post", "connect", "new", "logout", "schedule-off"):
            self.assertIn(f"action === '{action}'", source)
        for action in ("publish", "reject", "image", "webimage", "noimage"):
            self.assertIn(f"'{action}'", source)
        for form in ("login", "connect", "schedule", "profile", "edit", "create"):
            self.assertIn(f"action === '{form}'", source)


if __name__ == "__main__":
    unittest.main()
