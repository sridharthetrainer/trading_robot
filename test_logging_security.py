import logging

from logging_security import SecretRedactingFilter, redact_secrets


def test_redacts_bearer_and_telegram_tokens():
    text = redact_secrets(
        "Authorization: Bearer abc.def.ghi "
        "https://api.telegram.org/bot123456:ABC/sendMessage"
    )
    assert "abc.def.ghi" not in text
    assert "123456:ABC" not in text
    assert text.count("***") == 2


def test_filter_redacts_formatted_arguments():
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "Bearer %s", ("secret",), None)
    assert SecretRedactingFilter().filter(record)
    assert record.getMessage() == "Bearer ***"
