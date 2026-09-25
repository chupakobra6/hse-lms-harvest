import asyncio
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hse_lms_harvest import cli, credentials


@pytest.fixture
def helper(tmp_path: Path) -> Path:
    executable = tmp_path / "credential helper"
    executable.write_text(
        f"#!{sys.executable}\n"
        r"""import json
import os
import sys
from pathlib import Path

root = Path(__file__).parent
request = json.load(sys.stdin)
assert len(sys.argv) == 1
assert request['account'] in {'student@example.edu', 'netology@example.edu'}
assert request['service'] == 'codex-study-lms'
assert 'HSE_LMS_PASSWORD' not in os.environ
assert 'UNRELATED_API_TOKEN' not in os.environ
secret_file = root / ('fake-store' if request['account'] == 'student@example.edu' else 'fake-store-netology')
secret = request.get('secret') or (secret_file.read_text() if secret_file.exists() else '')
if secret:
    assert secret not in '\0'.join(sys.argv)
    assert secret not in '\0'.join(os.environ.values())
with (root / 'calls.jsonl').open('a') as calls:
    calls.write(json.dumps({'operation': request['operation'], 'keys': sorted(request)}) + '\n')
mode_file = root / 'mode'
mode = mode_file.read_text() if mode_file.exists() else ''
if mode == 'interaction-required':
    print(json.dumps({'error': 'interaction-required'}))
    sys.exit(1)
if mode == 'invalid':
    print(secret)
    print(secret, file=sys.stderr)
    sys.exit(2)
if request['operation'] == 'put':
    secret_file.write_text(request['secret'])
    print('{}')
elif request['operation'] == 'delete':
    secret_file.unlink(missing_ok=True)
    print('{}')
elif not secret_file.exists():
    print(json.dumps({'error': 'not-found'}))
    sys.exit(1)
elif request['operation'] == 'get':
    print(json.dumps({'secret': secret_file.read_text()}))
elif request['operation'] == 'check':
    print('{}')
else:
    sys.exit(3)
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


def test_credentials_cli_uses_stdin_helper_and_keeps_only_config(
    tmp_path: Path, helper: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env_file = tmp_path / "lms.env"
    secret = "fixture-password-α-123"
    env_file.write_text('HSE_LMS_PASSWORD="old-plaintext-fixture"\nOTHER="keep"\n')
    monkeypatch.setenv("HSE_LMS_PASSWORD", "ignored-env-fixture")
    monkeypatch.setenv("UNRELATED_API_TOKEN", "unrelated-env-fixture")
    monkeypatch.setattr(sys, "stdin", io.StringIO(secret + "\n"))
    assert (
        cli.main(
            [
                "credentials",
                "set",
                "--source",
                "smart_lms",
                "--username",
                "student@example.edu",
                "--credential-helper",
                str(helper),
                "--env-file",
                str(env_file),
                "--password-stdin",
            ]
        )
        == 0
    )
    values = credentials.read_env_file(env_file)
    assert values == {
        "SMART_LMS_USERNAME": "student@example.edu",
        "HSE_LMS_CREDENTIAL_HELPER": str(helper),
        "HSE_LMS_CREDENTIAL_SERVICE": "codex-study-lms",
        "OTHER": "keep",
    }
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert credentials.load_password("smart_lms", env_file=env_file) == secret
    assert credentials.load_password("smart_lms", "different@example.edu", env_file) is None
    assert (
        cli.main(["credentials", "status", "--source", "smart_lms", "--env-file", str(env_file)])
        == 0
    )
    assert (
        cli.main(["credentials", "delete", "--source", "smart_lms", "--env-file", str(env_file)])
        == 0
    )
    assert credentials.read_env_file(env_file) == {"OTHER": "keep"}
    assert not (tmp_path / "fake-store").exists()
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [call["operation"] for call in calls] == [
        "put",
        "get",
        "get",
        "check",
        "delete",
    ]
    assert [call["operation"] for call in calls if "secret" in call["keys"]] == ["put"]
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert secret not in env_file.read_text()


def test_one_env_keeps_two_accounts_independent(tmp_path: Path, helper: Path) -> None:
    env_file = tmp_path / ".env"
    credentials.store_password(
        "smart_lms", "student@example.edu", "smart-secret", env_file, credential_helper=helper
    )
    credentials.store_password(
        "netology", "netology@example.edu", "netology-secret", env_file, credential_helper=helper
    )
    values = credentials.read_env_file(env_file)
    assert values == {
        "SMART_LMS_USERNAME": "student@example.edu",
        "NETOLOGY_USERNAME": "netology@example.edu",
        "HSE_LMS_CREDENTIAL_HELPER": str(helper),
        "HSE_LMS_CREDENTIAL_SERVICE": "codex-study-lms",
    }
    assert credentials.load_password("smart_lms", env_file=env_file) == "smart-secret"
    assert credentials.load_password("netology", env_file=env_file) == "netology-secret"
    assert "secret" not in env_file.read_text()
    other_helper = tmp_path / "other-helper"
    other_helper.write_bytes(helper.read_bytes())
    other_helper.chmod(0o700)
    with pytest.raises(credentials.CredentialError, match="same configured credential helper"):
        credentials.store_password(
            "netology",
            "netology@example.edu",
            "replacement",
            env_file,
            credential_helper=other_helper,
        )
    assert credentials.load_password("smart_lms", env_file=env_file) == "smart-secret"
    assert credentials.load_password("netology", env_file=env_file) == "netology-secret"
    credentials.delete_password("netology", env_file)
    assert credentials.load_password("smart_lms", env_file=env_file) == "smart-secret"
    assert credentials.load_default_username("netology", env_file) is None
    assert (tmp_path / "fake-store").exists()
    assert not (tmp_path / "fake-store-netology").exists()


def test_netology_login_selects_its_account_from_shared_env(
    tmp_path: Path, helper: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    credentials.store_password(
        "smart_lms", "student@example.edu", "smart-secret", env_file, credential_helper=helper
    )
    credentials.store_password(
        "netology", "netology@example.edu", "netology-secret", env_file, credential_helper=helper
    )
    page = SimpleNamespace(goto=AsyncMock(), wait_for_load_state=AsyncMock())
    monkeypatch.setattr(cli, "page_looks_logged_in", AsyncMock(return_value=False))
    monkeypatch.setattr(cli, "maybe_click_login", AsyncMock())
    monkeypatch.setattr(cli, "save_screenshot", AsyncMock())
    login = AsyncMock(return_value=SimpleNamespace(url="https://netology.ru/profile"))
    monkeypatch.setattr(cli, "auto_login", login)
    args = cli.build_parser().parse_args(
        [
            "harvest",
            "--url",
            "https://netology.ru/profile/program/module/schedule",
            "--auto-login",
            "--headless",
            "--env-file",
            str(env_file),
        ]
    )
    asyncio.run(
        cli.ensure_logged_in(
            SimpleNamespace(),
            page,
            args.url,
            args,
            tmp_path,
            SimpleNamespace(log=Mock()),
            None,
            SimpleNamespace(error=AsyncMock()),
        )
    )
    assert login.await_args.args[2:4] == ("netology@example.edu", "netology-secret")


def configure(env_file: Path, helper: Path) -> None:
    credentials.write_env_file(
        env_file,
        {
            "SMART_LMS_USERNAME": "student@example.edu",
            "HSE_LMS_CREDENTIAL_HELPER": str(helper),
            "HSE_LMS_CREDENTIAL_SERVICE": "codex-study-lms",
        },
    )


def test_missing_secret_and_blocked_access_are_distinct(
    tmp_path: Path, helper: Path, capsys: pytest.CaptureFixture
) -> None:
    env_file = tmp_path / "lms.env"
    configure(env_file, helper)
    assert credentials.load_password("smart_lms", env_file=env_file) is None
    assert "No password stored" in credentials.credentials_status("smart_lms", env_file)
    before = env_file.read_bytes()
    (tmp_path / "mode").write_text("interaction-required")
    assert (
        cli.main(["credentials", "status", "--source", "smart_lms", "--env-file", str(env_file)])
        == 1
    )
    assert "blocked" in capsys.readouterr().err
    assert (
        cli.main(["credentials", "delete", "--source", "smart_lms", "--env-file", str(env_file)])
        == 1
    )
    assert env_file.read_bytes() == before


def test_plaintext_password_is_never_a_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "lms.env"
    env_file.write_text(
        'SMART_LMS_USERNAME="student@example.edu"\nHSE_LMS_PASSWORD="old-fixture"\n'
    )
    monkeypatch.setenv("HSE_LMS_PASSWORD", "env-fixture")
    with pytest.raises(credentials.CredentialError, match="credential-helper"):
        credentials.load_password("smart_lms", env_file=env_file)


def test_bad_helper_output_and_timeout_never_leak_secrets(
    tmp_path: Path, helper: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    env_file = tmp_path / "lms.env"
    configure(env_file, helper)
    secret = "sensitive-fixture-output"
    (tmp_path / "fake-store").write_text(secret)
    (tmp_path / "mode").write_text("invalid")
    assert (
        cli.main(["credentials", "status", "--source", "smart_lms", "--env-file", str(env_file)])
        == 1
    )
    output = capsys.readouterr()
    assert "invalid response" in output.err
    assert secret not in output.out + output.err

    def timeout(*args, **kwargs):
        assert args[0] == [str(helper)]
        assert kwargs["timeout"] == 10
        raise subprocess.TimeoutExpired(args[0], 10, output=secret, stderr=secret)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert (
        cli.main(["credentials", "status", "--source", "smart_lms", "--env-file", str(env_file)])
        == 1
    )
    output = capsys.readouterr()
    assert "10 seconds" in output.err
    assert secret not in output.out + output.err


def test_set_rejects_relative_helper_without_writing_config(tmp_path: Path) -> None:
    env_file = tmp_path / "lms.env"
    with pytest.raises(credentials.CredentialError, match="absolute path"):
        credentials.store_password(
            "smart_lms",
            "student@example.edu",
            "fixture",
            env_file,
            credential_helper=Path("helper"),
        )
    assert not env_file.exists()


@pytest.mark.parametrize("mode", ["missing", "interaction-required"])
def test_headless_login_does_not_wait_for_manual_auth_when_secret_unavailable(
    tmp_path: Path, helper: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    env_file = tmp_path / "lms.env"
    configure(env_file, helper)
    if mode == "interaction-required":
        (tmp_path / "mode").write_text(mode)
    page = SimpleNamespace(goto=AsyncMock(), wait_for_load_state=AsyncMock())
    diagnostics = SimpleNamespace(error=AsyncMock())
    manual_wait = AsyncMock()
    monkeypatch.setattr(cli, "page_looks_logged_in", AsyncMock(return_value=False))
    monkeypatch.setattr(cli, "maybe_click_login", AsyncMock())
    monkeypatch.setattr(cli, "save_screenshot", AsyncMock())
    monkeypatch.setattr(cli, "wait_for_logged_in", manual_wait)
    args = cli.build_parser().parse_args(
        [
            "harvest",
            "--url",
            "about:blank",
            "--auto-login",
            "--headless",
            "--env-file",
            str(env_file),
        ]
    )
    with pytest.raises(credentials.CredentialError):
        asyncio.run(
            cli.ensure_logged_in(
                SimpleNamespace(),
                page,
                "about:blank",
                args,
                tmp_path,
                SimpleNamespace(log=Mock()),
                None,
                diagnostics,
            )
        )
    manual_wait.assert_not_awaited()
    diagnostics.error.assert_awaited_once()
