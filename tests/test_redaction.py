"""Redaction, tested with planted secrets.

Each case here is a credential shape that has leaked out of a real codebase at
some point. The Bearer case is a regression test: an earlier version matched
`Authorization:` with the generic assignment rule and redacted only the word
"Bearer", leaving the token in the clear.
"""

import pytest

from orgono.app.core.redact import PLACEHOLDER, looks_secret, redact_obj, redact_text

PLANTED = [
    ("OPENAI_API_KEY=sk-proj-abcdefghijklmnop1234567890", "sk-proj-abcdefghijklmnop1234567890"),
    ("api_key: 'hunter2thisissecret'", "hunter2thisissecret"),
    ("password = \"correct-horse-battery\"", "correct-horse-battery"),
    ("Authorization: Bearer abcdefghij1234567890", "abcdefghij1234567890"),
    ('headers={"Authorization": "Bearer sk-or-v1-deadbeefcafebabe1234"}', "sk-or-v1-deadbeefcafebabe1234"),
    ("token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"),
    ("slack = xoxb-123456789012-abcdefghij", "xoxb-123456789012-abcdefghij"),
    ("GOOGLE=AIzaSyA1234567890123456789012345678901", "AIzaSyA1234567890123456789012345678901"),
    ("aws = AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("stripe_key = sk_live_abcdefghijklmnop1234", "sk_live_abcdefghijklmnop1234"),
    ("DB_URL = postgres://user:s3cr3tpassword@host:5432/db", "s3cr3tpassword"),
    ("jwt = eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
     "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
]


@pytest.mark.parametrize("text,secret", PLANTED)
def test_planted_secret_never_survives(text, secret):
    out, count = redact_text(text)
    assert secret not in out, f"secret survived redaction: {out}"
    assert count >= 1
    assert PLACEHOLDER in out


def test_private_key_block_is_removed():
    pem = ("-----BEGIN RSA PRIVATE KEY-----\n"
           "MIIEowIBAAKCAQEAxyz\nabcdef\n"
           "-----END RSA PRIVATE KEY-----")
    out, count = redact_text(pem)
    assert "MIIEowIBAAKCAQEAxyz" not in out
    assert count == 1


@pytest.mark.parametrize("benign", [
    "normal_code = compute(x) + 1",
    "def token_bucket(rate): return rate * 2",
    "# the password field is validated elsewhere",
    "import os",
    "authenticate(user)",
])
def test_ordinary_code_is_left_alone(benign):
    out, count = redact_text(benign)
    assert out == benign
    assert count == 0


def test_redaction_is_idempotent():
    once, _ = redact_text("API_KEY=sk-proj-abcdefghijklmnop1234")
    twice, count = redact_text(once)
    assert once == twice
    assert count == 0


def test_no_double_bracket_artifact():
    """Regression: the generic rule used to re-match a truncated placeholder."""
    out, _ = redact_text("OPENAI_API_KEY=sk-proj-abcdefghijklmnop1234567890")
    assert "]]" not in out
    assert out == "OPENAI_API_KEY=[REDACTED]"


def test_redact_obj_handles_secret_keys_and_nesting():
    out = redact_obj({"api_key": "hunter2", "nested": {"token": "abc"}, "ok": "fine",
                      "list": ["password: swordfish"]})
    assert out["api_key"] == PLACEHOLDER
    assert out["nested"]["token"] == PLACEHOLDER
    assert out["ok"] == "fine"
    assert "swordfish" not in out["list"][0]


def test_looks_secret():
    assert looks_secret("password: swordfish123")
    assert not looks_secret("just some prose")
