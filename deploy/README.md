# Развёртывание Mark на Ubuntu

Нужен Ubuntu 24.04 LTS, публичный IPv4, открытые входящие TCP-порты 22, 80 и 443.
Для Telegram polling и веб-студии используется один процесс Mark.

## Подготовка сервера

Создать системного пользователя `mark`, установить Python 3.12+, Node.js 22.12+, Git и Caddy. Клонировать репозиторий в `/opt/mark`, затем:

```sh
sudo -u mark python3 -m venv /opt/mark/.venv
sudo -u mark /opt/mark/.venv/bin/pip install --upgrade pip
sudo -u mark /opt/mark/.venv/bin/pip install -r /opt/mark/requirements.txt
cd /opt/mark && sudo -u mark npm ci
```

Создать `/etc/mark/mark.env` с правами `0600` и владельцем `root`. Перенести туда необходимые значения из локального `.env`; ключи и токены не передавать в командной строке или Git. Минимум:

```dotenv
TELEGRAM_BOT_TOKEN=<токен Telegram-бота>
MINIAPP_PORT=3000
TIMEZONE=Asia/Yekaterinburg
ROUTERAI_IMAGE_API_KEY=<необязательный общий ключ RouterAI для изображений>
ROUTERAI_IMAGE_MODEL=black-forest-labs/flux-3-image
ROUTERAI_IMAGE_ASPECT_RATIO=1:1
ROUTERAI_API_KEY=<необязательный общий ключ RouterAI; личный OpenAI API пользователь подключает в Telegram через /api>
MODEL_NAME=openai/gpt-6-luna
ROUTERAI_MAX_TOKENS=2400
```

Для Telegram Mini App задать `MINIAPP_URL=https://mark.example.com`. Если Mini App не используется, это значение можно не задавать.

Установить unit-файл:

```sh
sudo cp /opt/mark/deploy/mark.service /etc/systemd/system/mark.service
sudo systemctl daemon-reload
sudo systemctl enable --now mark
sudo systemctl status mark
```

## HTTPS и домен

Направить DNS A-запись домена на IP сервера, затем добавить блок из `Caddyfile.example` в `/etc/caddy/Caddyfile` (заменить `mark.example.com` на свой домен). Проверить и перезапустить Caddy:

```sh
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Открыть в панели VPS и локальном firewall порты 80 и 443. Не выставлять порт 3000 в интернет: Caddy проксирует HTTPS локально.

## Хранилище и обновления

Пользователь `mark` должен иметь доступ на запись в `/opt/mark` для SQLite, файлов состояния и медиа. Резервировать `/opt/mark/state.json`, `/opt/mark/users.sqlite3`, `/opt/mark/.auth` и `/opt/mark/instance/media`. Хранить копии вне сервера с ограниченным доступом.

Обновлять код только после резервной копии и проверки изменений:

```sh
cd /opt/mark
sudo -u mark git pull --ff-only
sudo -u mark npm ci
sudo -u mark /opt/mark/.venv/bin/pip install -r requirements.txt
sudo systemctl restart mark
journalctl -u mark -n 100 --no-pager
```

## Общий AI

Mark использует общий RouterAI для всех пользователей. Вход через ChatGPT отключён.
После изменения ключа или модели перезапустите службу. Память компании и архив диалогов
хранятся в `users.sqlite3`; включайте базу в резервное копирование.
