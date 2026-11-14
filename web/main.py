"""Python entry point for the Mini App and its Node.js API server."""

from pathlib import Path
import shutil
import subprocess
import sys


def main() -> int:
    project_dir = Path(__file__).resolve().parent
    server_file = project_dir / "server.js"
    node = shutil.which("node")

    if not server_file.is_file():
        print(f"Не найден серверный файл: {server_file}", file=sys.stderr)
        return 1
    if not node:
        print(
            "Не найден Node.js. Установите Node.js 18 или новее, "
            "затем снова запустите python main.py.",
            file=sys.stderr,
        )
        return 1

    print("Запускаю сайт и API. Откройте http://localhost:3000")
    print("Для остановки нажмите Ctrl+C.", flush=True)
    try:
        return subprocess.call([node, str(server_file)], cwd=project_dir)
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
