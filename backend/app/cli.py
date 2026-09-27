"""Interactive setup commands for the panel administrator."""

import argparse
from getpass import getpass
import sys

from app.auth.sessions import AuthService
from app.config import load_settings
from app.storage.db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wardogs panel administration")
    parser.add_argument("command", choices=["create-admin"])
    args = parser.parse_args(argv)
    if args.command == "create-admin":
        username = input("管理员用户名: ").strip()
        password = getpass("管理员密码: ")
        confirm = getpass("再次输入密码: ")
        if password != confirm:
            print("两次密码不一致", file=sys.stderr)
            return 1
        settings = load_settings()
        database = Database(settings.db_path)
        database.initialize()
        try:
            AuthService(database, settings).create_admin(username, password)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("管理员创建成功")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
