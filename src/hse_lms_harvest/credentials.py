from __future__ import annotations

import getpass
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

USERNAME_KEYS = {
    "smart_lms": "SMART_LMS_USERNAME",
    "netology": "NETOLOGY_USERNAME",
}
ENV_HELPER = "HSE_LMS_CREDENTIAL_HELPER"
ENV_SERVICE = "HSE_LMS_CREDENTIAL_SERVICE"
DEFAULT_SERVICE = "codex-study-lms"
DEFAULT_ENV_FILE = Path(".env")


class CredentialError(RuntimeError):
    pass


def store_password(
    source: str,
    username: str,
    password: str,
    env_file: Path = DEFAULT_ENV_FILE,
    *,
    credential_helper: Path,
) -> None:
    key = username_key(source)
    if not username:
        raise CredentialError("Username is empty.")
    if not password:
        raise CredentialError("Password is empty.")

    env_file = env_file.expanduser().resolve()
    env_file.parent.mkdir(parents=True, exist_ok=True)
    values = read_env_file(env_file)
    helper = str(helper_path(str(credential_helper)))
    if any(values.get(other_key) for other_key in USERNAME_KEYS.values() if other_key != key) and (
        values.get(ENV_HELPER) != helper or values.get(ENV_SERVICE) != DEFAULT_SERVICE
    ):
        raise CredentialError(
            "Both sources must use the same configured credential helper and service."
        )
    values[key] = username
    values[ENV_HELPER] = helper
    values[ENV_SERVICE] = DEFAULT_SERVICE
    call_helper(values, "put", username, secret=password)
    if call_helper(values, "get", username).get("secret") != password:
        raise CredentialError("Credential helper did not verify the stored password.")
    values.pop("HSE_LMS_PASSWORD", None)
    write_env_file(env_file, values)


def username_key(source: str) -> str:
    try:
        return USERNAME_KEYS[source]
    except KeyError:
        raise CredentialError(f"Unknown credential source: {source}.") from None


def load_default_username(source: str, env_file: Path = DEFAULT_ENV_FILE) -> str | None:
    return read_env_file(env_file).get(username_key(source))


def load_password(
    source: str, username: str | None = None, env_file: Path = DEFAULT_ENV_FILE
) -> str | None:
    values = read_env_file(env_file)
    stored_username = values.get(username_key(source))
    if username and stored_username != username:
        return None
    if not stored_username:
        return None
    response = call_helper(values, "get", stored_username)
    if response.get("error") == "not-found":
        return None
    secret = response.get("secret")
    if not isinstance(secret, str) or not secret:
        raise CredentialError("Credential helper returned no password.")
    return secret


def delete_password(source: str, env_file: Path = DEFAULT_ENV_FILE) -> None:
    env_file = env_file.expanduser().resolve()
    if not env_file.exists():
        return
    values = read_env_file(env_file)
    key = username_key(source)
    username = values.get(key)
    if username:
        call_helper(values, "delete", username)
    values.pop(key, None)
    if not any(values.get(other_key) for other_key in USERNAME_KEYS.values()):
        values.pop(ENV_HELPER, None)
        values.pop(ENV_SERVICE, None)
    values.pop("HSE_LMS_PASSWORD", None)
    write_env_file(env_file, values)


def credentials_status(source: str, env_file: Path = DEFAULT_ENV_FILE) -> str:
    env_file = env_file.expanduser().resolve()
    values = read_env_file(env_file)
    username = values.get(username_key(source))
    if not username:
        return f"No credentials configured in {env_file}."
    response = call_helper(values, "check", username)
    if response.get("error") == "not-found":
        return f"No password stored for {username}; run credentials set."
    return f"Credentials available for {username} through the configured helper."


def helper_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not value or not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise CredentialError(
            "Set --credential-helper to an absolute path to an executable credential helper."
        )
    return path


def call_helper(
    values: dict[str, str], operation: str, username: str, *, secret: str | None = None
) -> dict[str, str]:
    helper = helper_path(values.get(ENV_HELPER, ""))
    service = values.get(ENV_SERVICE)
    if not service:
        raise CredentialError("Credential service is not configured; run credentials set.")
    request = {"operation": operation, "account": username, "service": service}
    if secret is not None:
        request["secret"] = secret
    # The helper receives only stdin and a small non-secret process environment.
    helper_env = {
        key: os.environ[key]
        for key in ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL")
        if key in os.environ
    }
    try:
        result = subprocess.run(
            [str(helper)],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
            env=helper_env,
        )
    except subprocess.TimeoutExpired:
        raise CredentialError("Credential helper timed out after 10 seconds.") from None
    except OSError:
        raise CredentialError("Credential helper could not be started.") from None
    except UnicodeError:
        raise CredentialError("Credential helper returned an invalid response.") from None
    try:
        response = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise CredentialError("Credential helper returned an invalid response.") from None
    if not isinstance(response, dict):
        raise CredentialError("Credential helper returned an invalid response.")
    error = response.get("error")
    if error in {"locked", "interaction-required", "access-denied", "user-canceled"}:
        raise CredentialError(
            "Credential access is blocked. Repair or unlock the configured credential store "
            "before retrying; background login cannot request approval."
        )
    if error == "not-found":
        if operation == "put":
            raise CredentialError("Credential helper could not store the password.")
        return {"error": "not-found"}
    if result.returncode != 0 or error:
        raise CredentialError("Credential helper failed; inspect its configuration.")
    return response


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
