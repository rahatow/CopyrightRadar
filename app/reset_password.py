"""Forgot the page password? Remove it; the next visit to /setup asks for a new one.

    docker compose exec app python -m app.reset_password

Instagram token, ACRCloud key and all data stay. Open sessions end.
"""
from app.config import Settings
from app.storage.db import DB


def main() -> None:
    db = DB(Settings.from_env().data_dir / "copyright-radar.db")
    db.x("DELETE FROM kv WHERE key='password_hash'")
    db.set("password_version", (db.get("password_version", 0) or 0) + 1)
    db.audit("password_reset_cli")
    print("Password removed. Open the page and set a new one in step 1 of the setup.")


if __name__ == "__main__":
    main()
