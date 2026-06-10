"""One-off SECRET_KEY rotation with no data loss.

Decrypts every SecretBox-encrypted field with the OLD key, re-encrypts it with
a freshly generated key, commits, then persists the new key to the standard
key file so the running app reuses it.

Fields rotated:
  - Router.secret_enc                (RouterOS passwords)
  - SettingsKV "peer_private_key:*"  (WireGuard private keys)
  - SettingsKV "peer_preshared_key:*"(WireGuard preshared keys)
  - SettingsKV "tg_bot_token"        (Telegram bot token)

Values that do not decrypt with the OLD key (e.g. plaintext dev tokens) are
left untouched, so nothing is ever destroyed.

Usage (run with the app/DB idle):
    OLD_SECRET_KEY=change-me \
    DATABASE_URL=sqlite:///data/wgmik.db \
    python -m backend.migrate_secret_key
"""

import os
import secrets as _secrets
import stat
import sys
from pathlib import Path

OLD_SECRET_KEY = os.environ.get("OLD_SECRET_KEY", "change-me")
NEW_SECRET_KEY = os.environ.get("NEW_SECRET_KEY") or _secrets.token_urlsafe(48)

# Force the app to resolve to NEW_SECRET_KEY (explicit env wins, so importing
# settings does not generate or read a random key file).
os.environ["SECRET_KEY"] = NEW_SECRET_KEY

sys.path.append(str(Path(__file__).resolve().parent.parent))

from backend.security import SecretBox  # noqa: E402
from backend.settings import _default_secret_key_file  # noqa: E402
from backend.db import SessionLocal  # noqa: E402
from backend.models import Router, SettingsKV  # noqa: E402


def main() -> None:
    if OLD_SECRET_KEY == NEW_SECRET_KEY:
        print("OLD and NEW keys are identical; nothing to do.")
        return

    old_box = SecretBox(OLD_SECRET_KEY)
    new_box = SecretBox(NEW_SECRET_KEY)

    rotated = {"routers": 0, "peer_private_key": 0, "peer_preshared_key": 0, "tg_bot_token": 0}
    skipped = {"routers": 0, "settings_kv": 0}

    db = SessionLocal()
    try:
        for r in db.query(Router).all():
            enc = (r.secret_enc or "").strip()
            if not enc:
                continue
            dec = old_box.decrypt(enc)
            if dec is None:
                skipped["routers"] += 1
                continue
            r.secret_enc = new_box.encrypt(dec)
            rotated["routers"] += 1

        for kv in db.query(SettingsKV).all():
            key = kv.key or ""
            is_priv = key.startswith("peer_private_key:")
            is_psk = key.startswith("peer_preshared_key:")
            is_tg = key == "tg_bot_token"
            if not (is_priv or is_psk or is_tg):
                continue
            val = (kv.value or "").strip()
            if not val:
                continue
            dec = old_box.decrypt(val)
            if dec is None:
                skipped["settings_kv"] += 1
                continue
            kv.value = new_box.encrypt(dec)
            if is_priv:
                rotated["peer_private_key"] += 1
            elif is_psk:
                rotated["peer_preshared_key"] += 1
            else:
                rotated["tg_bot_token"] += 1

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    key_file = _default_secret_key_file()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(NEW_SECRET_KEY, encoding="utf-8")
    try:
        key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    print("Re-encrypted:", rotated)
    print("Skipped (not decryptable with OLD key, left untouched):", skipped)
    print(f"New key persisted to: {key_file}")


if __name__ == "__main__":
    main()
