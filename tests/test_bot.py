import base64
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as bot
import image_provider
import web_images
from user_database import UserDatabase


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-", dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        state = {"offset": 0, "chats": {}, "drafts": {}, "workspaces": {}}
        database = UserDatabase(Path(self.temp.name) / "users.sqlite3")
        for name, value in {
            "state": state, "workspaces": state["workspaces"], "STATE_FILE": Path(self.temp.name) / "state.json",
            "user_database": database, "publish_jobs": {}, "reset_requests": {}, "api_key_requests": set(),
            "chatgpt_logins": {},
        }.items():
            p = patch.object(bot, name, value)
            p.start()
            self.addCleanup(p.stop)
        provider = patch.object(bot, "ai_provider", side_effect=lambda chat_id: "chatgpt" if str(chat_id) in bot.workspaces else None)
        provider.start()
        self.addCleanup(provider.stop)
        self.calls = []
        self.count = 0

        def telegram(method, payload=None):
            self.calls.append((method, payload))
            self.count += 1
            return {"message_id": self.count}

        self.telegram = patch.object(bot, "tg", side_effect=telegram).start()
        patch.object(bot, "request_text", side_effect=bot.SetupError("AI disabled in tests")).start()
        patch.object(bot.subprocess, "run", side_effect=AssertionError("External process disabled in tests")).start()
        self.addCleanup(patch.stopall)

    def message(self, user, text):
        bot.handle_message({"chat": {"id": user, "type": "private"}, "from": {"id": user, "username": f"user{user}"}, "text": text})

    def workspace(self, user=1):
        bot.workspaces[str(user)] = {
            "channel": f"-100{user}", "channel_label": f"@channel{user}",
            "profile": {"name": f"Компания {user}", "description": "Промышленность"},
            "registration": {"display_name": "Тимур", "telegram_user_id": str(user), "registered_at": "2026-11-13"},
        }

    def draft(self, user=1, photo=None):
        self.workspace(user)
        draft = bot.send_draft(str(user), "Наша компания участвует в хакатоне.", photo_id=photo)
        draft_id = next(key for key, value in bot.state["drafts"].items() if value is draft)
        return draft_id, draft

    def callback(self, action, draft_id, user=1):
        self.button(f"{action}:{draft_id}", user)

    def button(self, data, user=1):
        bot.handle_callback({"id": "query", "data": data, "from": {"id": user},
                             "message": {"chat": {"type": "private", "id": user}, "message_id": 100}})

    def wait_for_publication(self, draft_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            jobs = [(job_id, job) for job_id, job in bot.publish_jobs.items() if job.get("draft_id") == draft_id]
            if jobs and jobs[-1][1]["status"] in ("done", "failed"):
                return jobs[-1]
            time.sleep(0.01)
        self.fail("Publication job did not finish")

    def wait_for_login_cleanup(self, user):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if str(user) not in bot.chatgpt_logins:
                return
            time.sleep(0.01)
        self.fail("ChatGPT login process was not released")

    def test_failed_chatgpt_login_releases_chat_and_allows_retry(self):
        class FailedLoginProcess:
            def __init__(self):
                self.stdout = io.StringIO('{"url":"https://auth.openai.com/example"}\n')
                self.stderr = io.StringIO("oauth_error:invalid_grant\n")
                self.returncode = 1

            def wait(self, timeout=None):
                return self.returncode

            def poll(self):
                return self.returncode

            def terminate(self):
                return None

            def kill(self):
                self.returncode = -9

        with patch.object(bot.subprocess, "Popen", side_effect=[FailedLoginProcess(), FailedLoginProcess()]) as popen:
            for attempt in (1, 2):
                bot.start_chatgpt_login("1")
                self.wait_for_login_cleanup("1")
                self.assertEqual(popen.call_count, attempt)

        sent_text = [payload["text"] for method, payload in self.calls if method == "sendMessage"]
        self.assertEqual(sum("истёк или уже использован" in text for text in sent_text), 2)

    def test_registration_isolated_persistent_and_idempotent_start(self):
        self.message(1, "/start")
        self.message(2, "/start")
        self.message(1, "timur@example.com")
        self.message(1, "secure-password-123")
        self.message(1, "Тимур")
        self.message(1, "СалаватСтекло")
        self.message(1, "Производство стекла, деловой стиль")
        self.message(2, "aliya@example.com")
        self.message(2, "secure-password-456")
        self.message(2, "Алия")
        self.assertNotIn("1", bot.workspaces)
        with patch.object(bot, "check_channel", return_value={"id": -1001, "username": "factory"}):
            self.message(1, "@factory")
        saved = json.loads(bot.STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["workspaces"]["1"]["profile"]["name"], "СалаватСтекло")
        self.assertEqual(saved["workspaces"]["1"]["registration"]["telegram_user_id"], "1")
        self.assertEqual(saved["setup"]["2"]["display_name"], "Алия")
        self.message(1, "/start")
        self.assertNotIn("1", bot.state["setup"])
        self.message(1, "/profile")
        self.assertIn("СалаватСтекло", self.calls[-1][1]["text"])

    def test_channel_denial_does_not_register(self):
        for text in ("/register", "timur@example.com", "secure-password-123", "Тимур", "Компания", "Описание"):
            self.message(1, text)
        with patch.object(bot, "check_channel", side_effect=bot.SetupError("Нет прав")):
            self.message(1, "@factory")
        self.assertNotIn("1", bot.workspaces)
        self.assertEqual(bot.state["setup"]["1"]["step"], "channel")

    def test_cancel_keeps_existing_profile(self):
        self.workspace()
        original = json.dumps(bot.workspaces["1"])
        self.message(1, "/setup")
        self.message(1, "Другое имя")
        self.message(1, "/cancel")
        self.assertEqual(json.dumps(bot.workspaces["1"]), original)

    def test_name_validation_and_resume(self):
        self.message(1, "/register")
        self.message(1, "timur@example.com")
        self.message(1, "secure-password-123")
        self.message(1, "x" * 81)
        self.assertEqual(bot.state["setup"]["1"]["step"], "name")
        saved = bot.read_json(bot.STATE_FILE, {})
        bot.state["setup"] = saved["setup"]
        self.message(1, "Тимур")
        self.assertEqual(bot.state["setup"]["1"]["step"], "company")

    def test_private_identity_required(self):
        bot.handle_message({"chat": {"id": 1, "type": "private"}, "from": {"id": 2}, "text": "/start"})
        self.assertEqual(self.calls, [])

    def test_no_key_preserves_text_draft(self):
        draft_id, draft = self.draft()
        with patch.object(bot, "images_configured", return_value=False):
            self.callback("image", draft_id)
        self.assertEqual(draft["status"], "pending")
        self.assertIn("/api", self.calls[-1][1]["text"])

    def test_generate_replaces_draft_and_repeated_click_does_not_regenerate(self):
        draft_id, old = self.draft()
        with patch.object(bot, "images_configured", return_value=True), patch.object(bot, "generate_image", return_value=b"image") as generate, patch.object(bot, "send_photo", return_value={"message_id": 99, "photo": [{"file_id": "photo-id"}]}) as upload:
            self.callback("image", draft_id)
            self.callback("image", draft_id)
        self.assertEqual(generate.call_count, 1)
        self.assertIn("Компания 1", generate.call_args.args[0])
        self.assertEqual(old["status"], "superseded")
        pending = bot.pending_for("1")
        self.assertEqual(pending["photo_id"], "photo-id")
        self.assertEqual(upload.call_args.args[2], old["text"])
        saved = bot.read_json(bot.STATE_FILE, {})
        self.assertTrue(any(d.get("photo_id") == "photo-id" for d in saved["drafts"].values()))

    def test_image_failure_keeps_original(self):
        draft_id, old = self.draft()
        with patch.object(bot, "images_configured", return_value=True), patch.object(bot, "generate_image", side_effect=image_provider.ImageError("Недоступно")):
            self.callback("image", draft_id)
        self.assertEqual(old["status"], "pending")
        self.assertEqual(len(bot.state["drafts"]), 1)

    def test_image_limit_blocks_provider(self):
        _, draft = self.draft()
        with patch.object(bot, "DAILY_USER_IMAGE_LIMIT", 0), patch.object(bot, "images_configured", return_value=True), patch.object(bot, "generate_image") as generate:
            with self.assertRaises(bot.SetupError):
                bot.add_draft_image("1", draft)
        generate.assert_not_called()

    def test_other_user_cannot_modify_or_publish_draft(self):
        draft_id, draft = self.draft()
        self.workspace(2)
        for action in ("approve", "image", "noimage", "reject"):
            self.callback(action, draft_id, user=2)
        self.assertEqual(draft["status"], "pending")
        self.assertEqual(len(bot.state["drafts"]), 1)

    def test_account_connect_and_reset_buttons(self):
        self.workspace()
        with patch.object(bot, "start_api_login") as api_login, patch.object(bot, "start_chatgpt_login") as chatgpt_login:
            self.button("connect_api")
            self.button("connect_chatgpt")
        api_login.assert_called_once_with("1")
        chatgpt_login.assert_called_once_with("1")
        self.button("reset_account")
        token = bot.reset_requests["1"]
        self.assertIn(f"reset_confirm:{token}", self.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"])
        self.button(f"reset_cancel:{token}")
        self.assertNotIn("1", bot.reset_requests)
        self.assertIn("Сброс отменён", self.calls[-1][1]["text"])
        self.button("reset_account")
        token = bot.reset_requests["1"]

        def reset_profile(chat_id):
            bot.workspaces.pop(chat_id, None)
            bot.state["drafts"].clear()

        with patch.object(bot, "reset_account", side_effect=reset_profile) as reset:
            self.button(f"reset_confirm:{token}")
        reset.assert_called_once_with("1")
        self.assertEqual(bot.state["setup"]["1"]["step"], "email")

    def test_photo_published_once_as_single_message(self):
        draft_id, draft = self.draft(photo="photo-id")
        self.calls.clear()
        self.callback("approve", draft_id)
        self.callback("approve", draft_id)
        job_id, job = self.wait_for_publication(draft_id)
        published = [(method, data) for method, data in self.calls if (data or {}).get("chat_id") == "-1001"]
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0][0], "sendPhoto")
        self.assertEqual(published[0][1]["caption"], draft["text"])
        self.assertEqual(published[0][1]["photo"], "photo-id")
        self.assertEqual(draft["status"], "published")
        self.assertEqual(bot.publish_job_status("1", job_id)["status"], "done")

    def test_uncertain_photo_send_cannot_be_retried(self):
        draft_id, draft = self.draft(photo="photo-id")
        original = self.telegram.side_effect
        def timeout(method, payload=None):
            if method == "sendPhoto" and payload["chat_id"] == "-1001":
                raise TimeoutError()
            return original(method, payload)
        self.telegram.side_effect = timeout
        self.callback("approve", draft_id)
        self.callback("approve", draft_id)
        self.wait_for_publication(draft_id)
        self.assertEqual(draft["status"], "publication_unknown")
        sends = [call for call in self.telegram.call_args_list if call.args[0] == "sendPhoto" and call.args[1]["chat_id"] == "-1001"]
        self.assertEqual(len(sends), 1)

    def test_remove_image_retires_old_preview(self):
        draft_id, old = self.draft(photo="photo-id")
        self.callback("noimage", draft_id)
        self.assertEqual(old["status"], "superseded")
        self.assertNotIn("photo_id", bot.pending_for("1"))

    def test_reject_never_publishes(self):
        draft_id, old = self.draft(photo="photo-id")
        self.callback("reject", draft_id)
        self.callback("approve", draft_id)
        self.assertEqual(old["status"], "rejected")
        self.assertFalse(any((data or {}).get("chat_id") == -1001 for _, data in self.calls))

    def test_text_revision_keeps_image(self):
        _, old = self.draft(photo="photo-id")
        with patch.object(bot, "openai_text", return_value="Обновлённый пост."):
            self.message(1, "переделай: короче")
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(bot.pending_for("1")["photo_id"], "photo-id")

    def test_prompt_with_image_routes_to_search(self):
        self.workspace()
        with patch.object(bot, "openai_text", return_value="Пост."), patch.object(bot, "add_web_image") as add:
            self.message(1, "/post о хакатоне с картинкой")
        add.assert_called_once()
        self.assertEqual(add.call_args.args[0], "1")

    def test_long_caption_and_clarification_rejected(self):
        self.assertIsNone(bot.post_body("ПОСТ\n" + "я" * 901))
        self.assertIsNone(bot.post_body("ПОСТ\n" + "😀" * 451))
        self.assertIsNone(bot.post_body("ПОСТ\nПришлите описание задания?"))
        self.assertEqual(bot.post_body("ПОСТ\nМы участвуем в хакатоне."), "Мы участвуем в хакатоне.")
        engaging = "Что общего у стекла и кода?\n\nСалаватСтекло предоставило задание для хакатона «Кибер 102»."
        self.assertEqual(bot.post_body("ПОСТ\n" + engaging), engaging)

    def test_multipart_upload(self):
        response = io.BytesIO(json.dumps({"ok": True, "result": {"message_id": 7, "photo": [{"file_id": "id"}]}}).encode())
        with patch.object(bot.urllib.request, "urlopen", return_value=response) as request:
            result = bot.send_photo("1", b"\xff\xd8\xffimage", "Текст", {"inline_keyboard": []})
        data = request.call_args.args[0].data
        self.assertIn(b'name="photo"; filename="post.jpg"', data)
        self.assertIn("Текст".encode(), data)
        self.assertIn(b"\xff\xd8\xffimage", data)
        self.assertEqual(result["message_id"], 7)

    def test_captioned_photo_used_without_search_or_generation(self):
        self.workspace()
        message = {"chat": {"id": 1, "type": "private"}, "from": {"id": 1},
                   "photo": [{"file_id": "small"}, {"file_id": "original"}], "caption": "Пост про хакатон"}
        with patch.object(bot, "openai_text", return_value="Наш пост."), patch.object(bot, "add_web_image") as search, patch.object(bot, "add_draft_image") as generate:
            bot.handle_message(message)
        self.assertEqual(bot.pending_for("1")["photo_id"], "original")
        self.assertEqual(bot.pending_for("1")["photo_source"]["kind"], "user")
        search.assert_not_called()
        generate.assert_not_called()

    def test_photo_without_caption_waits_for_next_request(self):
        self.workspace()
        bot.handle_message({"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "photo": [{"file_id": "original"}]})
        self.assertIsNone(bot.pending_for("1"))
        self.assertEqual(bot.read_json(bot.STATE_FILE, {})["uploads"]["1"]["file_id"], "original")
        with patch.object(bot, "openai_text", return_value="Текст по заданию."), patch.object(bot, "add_web_image") as search:
            self.message(1, "О нашем производстве")
        self.assertEqual(bot.pending_for("1")["photo_id"], "original")
        self.assertNotIn("1", bot.state["uploads"])
        search.assert_not_called()

    def test_photo_alone_replaces_pending_photo_without_ai(self):
        _, old = self.draft(photo="old")
        bot.handle_message({"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "photo": [{"file_id": "new"}]})
        self.assertEqual(bot.pending_for("1")["photo_id"], "new")
        self.assertEqual(bot.pending_for("1")["text"], old["text"])
        self.assertEqual(old["status"], "superseded")

    def test_cancel_removes_waiting_upload(self):
        self.workspace()
        bot.state["uploads"] = {"1": {"kind": "photo", "file_id": "original"}}
        self.message(1, "/cancel")
        self.assertNotIn("1", bot.state["uploads"])

    def test_document_uses_new_text_not_previous_draft(self):
        _, old = self.draft()
        with patch.object(bot, "download_user_image", return_value=b"\xff\xd8\xffdata"), patch.object(bot, "send_photo", return_value={"message_id": 99, "photo": [{"file_id": "uploaded"}]}) as upload:
            bot.prepare_post("1", "Совсем новый пост.", "", replaces=old, upload={"kind": "document", "file_id": "doc"})
        self.assertEqual(upload.call_args.args[2], "Совсем новый пост.")
        self.assertEqual(old["status"], "superseded")

    def test_text_only_skips_all_image_services(self):
        self.workspace()
        with patch.object(bot, "add_web_image") as search, patch.object(bot, "add_draft_image") as generate:
            bot.prepare_post("1", "Пост.", "Напиши пост без картинки")
        search.assert_not_called()
        generate.assert_not_called()

    def test_auto_failure_keeps_generated_text(self):
        self.workspace()
        with patch.object(bot, "add_web_image", side_effect=bot.SetupError("No suitable photo")):
            bot.prepare_post("1", "Готовый текст.", "Тема")
        self.assertEqual(bot.pending_for("1")["text"], "Готовый текст.")

    def test_auto_success_shows_only_one_review_draft(self):
        self.workspace()
        def add(chat_id, draft, request):
            return bot.attach_image_bytes(chat_id, draft, b"image", {"kind": "generated"})
        with patch.object(bot, "add_web_image", side_effect=add), patch.object(bot, "send_photo", return_value={"message_id": 99, "photo": [{"file_id": "new"}]}):
            bot.prepare_post("1", "Готовый текст.", "Тема")
        self.assertEqual(len(bot.state["drafts"]), 1)
        self.assertFalse(any(method == "sendMessage" and "Черновик" in data.get("text", "") for method, data in self.calls))

    def test_web_search_fallback_generates_only_when_needed(self):
        _, draft = self.draft()
        with patch.object(bot, "request_text", return_value='{"query":"glass factory","generate":false}'), patch.object(bot, "search_images", return_value=[]), patch.object(bot, "images_configured", return_value=True), patch.object(bot, "add_draft_image", return_value={"status": "pending"}) as generate:
            bot.add_web_image("1", draft)
        generate.assert_called_once()

    def test_search_button_does_not_generate_without_match(self):
        _, draft = self.draft()
        with patch.object(bot, "request_text", return_value='{"query":"glass factory","generate":false}'), patch.object(bot, "search_images", return_value=[]), patch.object(bot, "add_draft_image") as generate:
            with self.assertRaises(bot.SetupError):
                bot.add_web_image("1", draft, fallback=False)
        generate.assert_not_called()

    def test_web_choice_preserves_source_and_skips_generation(self):
        _, draft = self.draft()
        candidate = {"kind": "web", "url": "https://commons.wikimedia.org/?curid=1", "download_url": "https://upload.wikimedia.org/x.jpg", "title": "Factory", "description": "Glass", "license": "CC0"}
        with patch.object(bot, "request_text", side_effect=['{"query":"glass factory"}', '{"index":0}']), patch.object(bot, "search_images", return_value=[candidate]), patch.object(bot, "download_image", return_value=b"image"), patch.object(bot, "send_photo", return_value={"message_id": 99, "photo": [{"file_id": "web"}]}), patch.object(bot, "add_draft_image") as generate:
            result = bot.add_web_image("1", draft)
        self.assertEqual(result["photo_source"]["url"], candidate["url"])
        self.assertNotIn("download_url", result["photo_source"])
        generate.assert_not_called()

    def test_source_in_published_photo_buttons(self):
        draft_id, draft = self.draft(photo="web")
        draft["photo_source"] = {"kind": "web", "url": "https://commons.wikimedia.org/?curid=1"}
        self.callback("approve", draft_id)
        self.wait_for_publication(draft_id)
        published = [data for method, data in self.calls if method == "sendPhoto" and data["chat_id"] == "-1001"][0]
        self.assertEqual(published["reply_markup"]["inline_keyboard"][0][0]["url"], draft["photo_source"]["url"])

    def test_group_album_does_not_generate_multiple_posts(self):
        self.workspace()
        message = {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "photo": [{"file_id": "first"}], "caption": "Тема", "media_group_id": "album"}
        with patch.object(bot, "openai_text", return_value="Пост.") as ai:
            bot.handle_message(message)
            bot.handle_message({**message, "caption": "", "photo": [{"file_id": "second"}]})
        ai.assert_called_once()
        self.assertEqual(bot.pending_for("1")["photo_id"], "first")


class WebImagesTests(unittest.TestCase):
    def test_filters_license_host_and_size(self):
        def page(number, license_name="CC0", url="https://upload.wikimedia.org/photo.jpg", width=1200):
            return {"pageid": number, "index": number, "title": "File:Glass.jpg", "imageinfo": [{"mime": "image/jpeg", "width": width, "height": 900, "thumburl": url, "extmetadata": {"LicenseShortName": {"value": license_name}}}]}
        pages = {str(i): p for i, p in enumerate([page(1), page(2, "CC BY-NC 4.0"), page(3, url="http://127.0.0.1/private"), page(4, width=100)])}
        response = io.BytesIO(json.dumps({"query": {"pages": pages}}).encode())
        with patch.object(web_images.urllib.request, "urlopen", return_value=response):
            results = web_images.search_images("glass")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://commons.wikimedia.org/?curid=1")

    def test_untrusted_download_rejected(self):
        with self.assertRaises(web_images.WebImageError):
            web_images.download_image("https://example.org/private")

    def test_image_redirect_host_restricted(self):
        with self.assertRaises(web_images.WebImageError):
            web_images.ImageRedirectHandler().redirect_request(None, None, 302, "", {}, "http://127.0.0.1/")


class ImageProviderTests(unittest.TestCase):
    def test_missing_key_does_not_call_api(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch.object(image_provider.urllib.request, "urlopen") as request:
            with self.assertRaises(image_provider.ImageError):
                image_provider.generate_image("prompt", "1")
        request.assert_not_called()

    def test_image_request_and_decode(self):
        image = b"\xff\xd8\xffjpeg"
        response = io.BytesIO(json.dumps({"data": [{"b64_json": base64.b64encode(image).decode()}]}).encode())
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(image_provider.urllib.request, "urlopen", return_value=response) as request:
            self.assertEqual(image_provider.generate_image("prompt", "123"), image)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload["output_format"], "jpeg")
        self.assertEqual(payload["n"], 1)
        self.assertNotEqual(payload["safety_identifier"], "123")

    def test_http_error_hides_key(self):
        error = urllib.error.HTTPError("https://example.test", 401, "test-key", {}, io.BytesIO(b"test-key"))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(image_provider.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(image_provider.ImageError) as caught:
                image_provider.generate_image("prompt", "1")
        self.assertNotIn("test-key", str(caught.exception))

    def test_invalid_image_rejected(self):
        response = io.BytesIO(json.dumps({"data": [{"b64_json": "invalid"}]}).encode())
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(image_provider.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(image_provider.ImageError):
                image_provider.generate_image("prompt", "1")


if __name__ == "__main__":
    unittest.main()
