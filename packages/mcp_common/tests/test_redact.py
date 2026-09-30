"""T-013: mcp_common.redact — secret scrubbing (ADR-0015)."""

from __future__ import annotations

from mcp_common.redact import scrub


def test_scrub_returns_unchanged_text_with_zero_count_when_no_secret() -> None:
    text, count = scrub("Module thanh toán xử lý retry 3 lần.")
    assert count == 0
    assert text == "Module thanh toán xử lý retry 3 lần."


def test_scrub_disabled_is_explicit_bypass() -> None:
    secret_line = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"
    text, count = scrub(secret_line, disabled=True)
    assert count == 0
    assert "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY" in text


def test_scrub_env_style_line_is_redacted() -> None:
    text, count = scrub("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY")
    assert count >= 1
    assert "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY" not in text
    assert "«redacted:" in text


def test_scrub_dotenv_style_multiline_content() -> None:
    dotenv_content = (
        "DB_HOST=localhost\n"
        "DB_PASSWORD=sup3rSecretPass!\n"
        "API_TOKEN=abcdef1234567890\n"
    )
    text, count = scrub(dotenv_content)
    assert count >= 2
    assert "sup3rSecretPass!" not in text
    assert "abcdef1234567890" not in text
    assert "DB_HOST=localhost" in text  # non-secret line untouched


def test_scrub_labeled_inline_secret() -> None:
    text, count = scrub('config: password: "hunter2hunter2"')
    assert count >= 1
    assert "hunter2hunter2" not in text


def test_scrub_aws_access_key_id() -> None:
    text, count = scrub("key id is AKIAIOSFODNN7EXAMPLE in the log line")
    assert count == 1
    assert "AKIAIOSFODNN7EXAMPLE" not in text
    assert "«redacted:aws_access_key_id»" in text


def test_scrub_jwt() -> None:
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    text, count = scrub(f"Authorization uses token {jwt}")
    assert count >= 1
    assert jwt not in text


def test_scrub_bearer_token() -> None:
    text, count = scrub("curl -H 'Authorization: Bearer abcd1234efgh5678ijkl'")
    assert count == 1
    assert "abcd1234efgh5678ijkl" not in text


def test_scrub_gitlab_token() -> None:
    text, count = scrub("remote: glpat-AbCdEfGhIjKlMnOpQrSt used for push")
    assert count == 1
    assert "glpat-AbCdEfGhIjKlMnOpQrSt" not in text


def test_scrub_private_key_block() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAK3example1234567890abcdefghijklmno\n"
        "-----END RSA PRIVATE KEY-----"
    )
    text, count = scrub(f"id_rsa contents:\n{pem}\nend of file")
    assert count == 1
    assert "MIIBOgIBAAJBAK3example1234567890abcdefghijklmno" not in text
    assert "end of file" in text


def test_scrub_high_entropy_long_token_without_label() -> None:
    random_token = "aZ9kQ7mN3pR8sT1vW5xY2bC4dE6fG0hJ8kL2mN4pQ6rS8tU"
    text, count = scrub(f"cache key = {random_token}")
    assert count >= 1
    assert random_token not in text


def test_scrub_does_not_flag_short_or_low_entropy_runs() -> None:
    text, count = scrub("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert count == 0


def test_scrub_count_matches_number_of_distinct_secrets() -> None:
    text, count = scrub(
        "AKIAIOSFODNN7EXAMPLE and glpat-AbCdEfGhIjKlMnOpQrSt in the same line"
    )
    assert count == 2


def test_scrub_empty_string_is_noop() -> None:
    text, count = scrub("")
    assert text == ""
    assert count == 0
