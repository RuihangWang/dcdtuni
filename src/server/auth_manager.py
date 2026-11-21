"""Standalone authentication manager — no HTTP awareness.

Provides password hashing, user CRUD, session management, and
escalating IP lockout with file-backed persistence.
"""

import fcntl
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
from security import CredentialsError, load_admin_credentials

SESSION_TTL = timedelta(hours=12)

# ---------------------------------------------------------------------------
# Password hashing helpers
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return ``salt_hex$hash_hex`` using PBKDF2-SHA256 (100 000 rounds)."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time verification of *password* against *stored_hash*."""
    try:
        salt_hex, hash_hex = stored_hash.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return secrets.compare_digest(dk.hex(), hash_hex)


# ---------------------------------------------------------------------------
# Dummy hash used for constant-time reject of non-existent users
# ---------------------------------------------------------------------------
_DUMMY_HASH = hash_password("__dummy__")

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_VALID_ROLES = {"admin", "viewer"}


# ---------------------------------------------------------------------------
# AuthManager
# ---------------------------------------------------------------------------

class AuthManager:
    """File-backed auth manager with users, sessions, and IP lockout."""

    # Default lockout thresholds: (failure_count, lock_seconds | None=permanent)
    _lockout_thresholds = [
        (5, 300),       # 5 min
        (10, 3600),     # 1 hr
        (15, 86400),    # 1 day
        (20, None),     # permanent
    ]

    def __init__(self, auth_dir: str | Path, log_path: str | Path | None = None, *, admin_credentials: tuple[str, str] | None = None):
        self._admin_credentials = admin_credentials
        self._auth_dir = Path(auth_dir)
        self._auth_dir.mkdir(parents=True, exist_ok=True)

        self._users_file = self._auth_dir / "users.json"
        self._sessions_file = self._auth_dir / "sessions.json"
        self._bans_file = self._auth_dir / "ip_bans.json"

        self._users_lock = threading.Lock()
        self._sessions_lock = threading.Lock()
        self._ip_lock = threading.Lock()

        # In-memory state
        self._users: dict[str, dict] = {}
        self._sessions: dict[str, dict] = {}
        self._ip_bans: dict[str, dict] = {}  # permanent bans
        self._ip_fails: dict[str, dict] = {}  # {ip: {count, locked_until}}

        # Logging
        self._logger = logging.getLogger('stlgen.auth.' + hashlib.sha256(str(self._auth_dir.resolve()).encode()).hexdigest()[:16])
        if not self._logger.handlers:
            self._logger.setLevel(logging.DEBUG)
            sh = logging.StreamHandler()
            sh.setLevel(logging.INFO)
            self._logger.addHandler(sh)
            if log_path:
                fh = RotatingFileHandler(
                    str(log_path), maxBytes=10 * 1024 * 1024, backupCount=5
                )
                fh.setLevel(logging.DEBUG)
                self._logger.addHandler(fh)

        # Load persisted data
        self._load_users()
        self._load_sessions()
        self._load_bans()

    # ------------------------------------------------------------------
    # Logging helper
    # ------------------------------------------------------------------
    def log(self, msg: str) -> None:
        self._logger.info(msg)

    # ------------------------------------------------------------------
    # File I/O with flock + atomic write
    # ------------------------------------------------------------------
    def _read_json(self, path: Path) -> dict | list:
        if not path.exists():
            return {}
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _write_json(self, path: Path, data) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self._auth_dir), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
                fcntl.flock(f, fcntl.LOCK_UN)
            os.replace(tmp_path, str(path))
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    # ------------------------------------------------------------------
    # User persistence
    # ------------------------------------------------------------------
    def _load_users(self) -> None:
        if self._users_file.exists():
            data = self._read_json(self._users_file)
            self._users = data if isinstance(data, dict) else {}
            if not self._users:
                self._create_default_admin()
        else:
            # First startup — create default admin
            self._users = {}
            self._create_default_admin()

    def _create_default_admin(self) -> None:
        credentials = self._admin_credentials
        if credentials is None:
            credentials = load_admin_credentials(os.environ, None)
        username, password = credentials
        if not isinstance(username, str) or not _USERNAME_RE.fullmatch(username) or not isinstance(password, str) or not password:
            raise CredentialsError("invalid explicit administrator credential format")
        now = datetime.now(timezone.utc).isoformat()
        self._users[username] = {
            "password_hash": hash_password(password),
            "role": "admin",
            "created_at": now,
        }
        self._save_users()
        self.log("Administrator initialized from explicit credentials")


    def _save_users(self) -> None:
        self._write_json(self._users_file, self._users)

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------
    def _load_sessions(self) -> None:
        if self._sessions_file.exists():
            data = self._read_json(self._sessions_file)
            self._sessions = data if isinstance(data, dict) else {}
        else:
            self._sessions = {}

    def _save_sessions(self) -> None:
        self._write_json(self._sessions_file, self._sessions)

    # ------------------------------------------------------------------
    # Ban persistence
    # ------------------------------------------------------------------
    def _load_bans(self) -> None:
        if self._bans_file.exists():
            data = self._read_json(self._bans_file)
            self._ip_bans = data if isinstance(data, dict) else {}
        else:
            self._ip_bans = {}

    def _save_bans(self) -> None:
        self._write_json(self._bans_file, self._ip_bans)

    # ==================================================================
    # User CRUD
    # ==================================================================

    def list_users(self) -> list[dict]:
        with self._users_lock:
            return [
                {
                    "username": uname,
                    "role": udata["role"],
                    "created_at": udata.get("created_at", ""),
                }
                for uname, udata in self._users.items()
            ]

    def authenticate(
        self, username: str, password: str
    ) -> tuple[bool, Optional[dict]]:
        with self._users_lock:
            user = self._users.get(username)
        if user is None:
            # Constant-time: still run verify against dummy hash
            verify_password(password, _DUMMY_HASH)
            return False, None
        if verify_password(password, user["password_hash"]):
            return True, {"username": username, "role": user["role"]}
        return False, None

    def create_user(
        self, username: str, password: str, role: str
    ) -> tuple[bool, str]:
        if not _USERNAME_RE.match(username):
            return False, "Invalid username: 1-32 chars of [A-Za-z0-9_] required"
        if role not in _VALID_ROLES:
            return False, f"Invalid role: must be one of {_VALID_ROLES}"
        with self._users_lock:
            if username in self._users:
                return False, f"User '{username}' already exists"
            now = datetime.now(timezone.utc).isoformat()
            self._users[username] = {
                "password_hash": hash_password(password),
                "role": role,
                "created_at": now,
            }
            self._save_users()
        self.log(f"User created: {username} (role={role})")
        return True, "User created"

    def delete_user(
        self, username: str, acting_user: str
    ) -> tuple[bool, str]:
        if username == acting_user:
            return False, "Cannot delete yourself"
        with self._users_lock:
            if username not in self._users:
                return False, f"User '{username}' not found"
            del self._users[username]
            self._save_users()
        self._delete_sessions_for_user(username)
        self.log(f"User deleted: {username} (by {acting_user})")
        return True, "User deleted"

    def change_password(
        self, username: str, new_password: str
    ) -> tuple[bool, str]:
        with self._users_lock:
            if username not in self._users:
                return False, f"User '{username}' not found"
            self._users[username]["password_hash"] = hash_password(new_password)
            self._save_users()
        self._delete_sessions_for_user(username)
        self.log(f"Password changed for user: {username}")
        return True, "Password changed"

    def change_role(
        self, username: str, new_role: str, acting_user: str
    ) -> tuple[bool, str]:
        if username == acting_user:
            return False, "Cannot change your own role"
        if new_role not in _VALID_ROLES:
            return False, f"Invalid role: must be one of {_VALID_ROLES}"
        with self._users_lock:
            if username not in self._users:
                return False, f"User '{username}' not found"
            self._users[username]["role"] = new_role
            self._save_users()
        # Update role in active sessions
        with self._sessions_lock:
            changed = False
            for token, sess in self._sessions.items():
                if sess["username"] == username:
                    sess["role"] = new_role
                    changed = True
            if changed:
                self._save_sessions()
        self.log(f"Role changed for {username}: {new_role} (by {acting_user})")
        return True, "Role changed"

    # ==================================================================
    # Session management
    # ==================================================================

    def create_session(self, username: str, role: str, ip: str, *, token: str | None = None) -> str:
        token = secrets.token_hex(32) if token is None else token
        if not isinstance(token, str) or len(token) < 32 or not token.isascii():
            raise ValueError("session token must have an explicit cryptographic format")
        now = datetime.now(timezone.utc).isoformat()
        sess = {
            "username": username,
            "role": role,
            "ip": ip,
            "created_at": now,
        }
        with self._sessions_lock:
            self._sessions[token] = sess
            self._save_sessions()
        self.log(f"Session created for {username} from {ip}")
        return token

    def validate_session(self, token):
        if not isinstance(token, str) or not token:
            return None
        sess = self._sessions.get(token)
        if not isinstance(sess, dict):
            return None
        try:
            ts = datetime.fromisoformat(sess.get('created_at', ''))
            if ts.tzinfo is None or datetime.now(timezone.utc) - ts > SESSION_TTL:
                raise ValueError('invalid or expired session time')
            if ts > datetime.now(timezone.utc):
                raise ValueError('session time is in the future')
            if sess.get('role') not in _VALID_ROLES or not isinstance(sess.get('username'), str):
                raise ValueError('invalid session principal')
        except (ValueError, TypeError):
            with self._sessions_lock:
                self._sessions.pop(token, None)
                self._save_sessions()
            return None
        return dict(sess)

    def delete_session(self, token: str) -> bool:
        with self._sessions_lock:
            if token in self._sessions:
                del self._sessions[token]
                self._save_sessions()
                return True
        return False

    def delete_session_by_prefix(self, prefix: str) -> bool:
        with self._sessions_lock:
            to_del = [t for t in self._sessions if t[:8] == prefix]
            if not to_del:
                return False
            for t in to_del:
                del self._sessions[t]
            self._save_sessions()
        return True

    def list_sessions(self) -> list[dict]:
        with self._sessions_lock:
            return [
                {
                    "token_prefix": token[:8],
                    "username": sess["username"],
                    "ip": sess["ip"],
                    "created_at": sess["created_at"],
                }
                for token, sess in self._sessions.items()
            ]

    def _delete_sessions_for_user(self, username: str) -> None:
        with self._sessions_lock:
            to_del = [
                t for t, s in self._sessions.items() if s["username"] == username
            ]
            for t in to_del:
                del self._sessions[t]
            if to_del:
                self._save_sessions()

    # ==================================================================
    # Escalating IP lockout
    # ==================================================================

    def check_ip_banned(self, ip: str) -> tuple[bool, Optional[str]]:
        # Check permanent bans first
        with self._ip_lock:
            if ip in self._ip_bans:
                return True, "Permanently banned"
            info = self._ip_fails.get(ip)
            if info and info.get("locked_until"):
                if info["locked_until"] == "permanent":
                    return True, "Permanently banned"
                if time.time() < info["locked_until"]:
                    remaining = int(info["locked_until"] - time.time()) + 1
                    return True, f"Locked out. Retry in {remaining}s"
                # Lock expired — don't reset count, just allow attempt
        return False, None

    def record_login_failure(self, ip: str) -> None:
        with self._ip_lock:
            if ip not in self._ip_fails:
                self._ip_fails[ip] = {"count": 0, "locked_until": None}
            rec = self._ip_fails[ip]
            rec["count"] += 1
            count = rec["count"]

            # Check thresholds from highest to lowest
            for threshold, duration in reversed(self._lockout_thresholds):
                if count >= threshold:
                    if duration is None:
                        # Permanent ban
                        rec["locked_until"] = "permanent"
                        self._ip_bans[ip] = {
                            "level": "permanent",
                            "locked_until": "permanent",
                            "banned_at": datetime.now(timezone.utc).isoformat(),
                        }
                        self._save_bans()
                        self.log(f"IP permanently banned: {ip}")
                    else:
                        rec["locked_until"] = time.time() + duration
                    break

    def record_login_success(self, ip: str) -> None:
        with self._ip_lock:
            if ip in self._ip_fails:
                del self._ip_fails[ip]

    def list_bans(self) -> list[dict]:
        results = []
        with self._ip_lock:
            # Permanent bans from file
            for ip, info in self._ip_bans.items():
                results.append({
                    "ip": ip,
                    "level": "permanent",
                    "locked_until": "permanent",
                })
            # Temporary locks from memory (active only)
            now = time.time()
            for ip, info in self._ip_fails.items():
                if ip in self._ip_bans:
                    continue  # already listed
                lu = info.get("locked_until")
                if lu and lu != "permanent" and lu > now:
                    results.append({
                        "ip": ip,
                        "level": "temporary",
                        "locked_until": datetime.fromtimestamp(
                            lu, tz=timezone.utc
                        ).isoformat(),
                    })
        return results

    def unban_ip(self, ip: str) -> bool:
        with self._ip_lock:
            removed = False
            if ip in self._ip_bans:
                del self._ip_bans[ip]
                self._save_bans()
                removed = True
            if ip in self._ip_fails:
                del self._ip_fails[ip]
                removed = True
            if removed:
                self.log(f"IP unbanned: {ip}")
            return removed
