from __future__ import annotations

import getpass
import os
import stat
import sys
from pathlib import Path

ENV_USERNAME = "HSE_LMS_USERNAME"
ENV_PASSWORD = "HSE_LMS_PASSWORD"
DEFAULT_ENV_FILE = Path(".env")


class CredentialError(RuntimeError):
    pass


def store_password(username: str, password: str, env_file: Path = DEFAULT_ENV_FILE) -> None:
    if not username:
        raise CredentialError("Username is empty.")
    if not password:
        raise CredentialError("Password is empty.")

    env_file = env_file.expanduser().resolve()
    env_file.parent.mkdir(parents=True, exist_ok=True)
    values = read_env_file(env_file)
    values[ENV_USERNAME] = username
    values[ENV_PASSWORD] = password
    write_env_file(env_file, values)


def load_default_username(env_file: Path = DEFAULT_ENV_FILE) -> str | None:
    return os.environ.get(ENV_USERNAME) or read_env_file(env_file).get(ENV_USERNAME)


def load_password(username: str | None = None, env_file: Path = DEFAULT_ENV_FILE) -> str | None:
    values = read_env_file(env_file)
    stored_username = os.environ.get(ENV_USERNAME) or values.get(ENV_USERNAME)
    password = os.environ.get(ENV_PASSWORD) or values.get(ENV_PASSWORD)
    if username and stored_username != username:
        return None
    return password or None


def delete_password(env_file: Path = DEFAULT_ENV_FILE) -> None:
    env_file = env_file.expanduser().resolve()
    if not env_file.exists():
        return
    values = read_env_file(env_file)
    values.pop(ENV_USERNAME, None)
    values.pop(ENV_PASSWORD, None)
    write_env_file(env_file, values)


def credentials_status(env_file: Path = DEFAULT_ENV_FILE) -> str:
    env_file = env_file.expanduser().resolve()
    values = read_env_file(env_file)
    username = os.environ.get(ENV_USERNAME) or values.get(ENV_USERNAME)
    if not username:
        return f"No credentials stored in {env_file}."
    mode = env_file.stat().st_mode & 0o777 if env_file.exists() else 0
    return f"Credentials stored for {username} at {env_file} mode={mode:o}."


def read_password_from_user(*, password_stdin: bool) -> str:
    if password_stdin:
        return sys.stdin.read().strip()
    return getpass.getpass("LMS password: ")


def read_env_file(path: Path) -> dict[str, str]:
    path = path.expanduser().resolve()
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = unquote_env_value(value.strip())
    return values


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={quote_env_value(value)}" for key, value in sorted(values.items())]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value
