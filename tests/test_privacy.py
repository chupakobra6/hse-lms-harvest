from hse_lms_harvest.privacy import redact_url, strip_fragment


def test_redact_url_removes_sensitive_query_values() -> None:
    assert (
        redact_url("https://edu.hse.ru/login/logout.php?sesskey=abc&id=1")
        == "https://edu.hse.ru/login/logout.php?sesskey=%5BREDACTED%5D&id=1"
    )
    assert (
        redact_url("https://auth.hse.ru/oidc?state=s1&nonce=n1&client_id=lms")
        == "https://auth.hse.ru/oidc?state=%5BREDACTED%5D&nonce=%5BREDACTED%5D&client_id=lms"
    )


def test_strip_fragment_also_removes_forceview() -> None:
    assert (
        strip_fragment("https://edu.hse.ru/mod/url/view.php?id=1&forceview=1#maincontent")
        == "https://edu.hse.ru/mod/url/view.php?id=1"
    )
