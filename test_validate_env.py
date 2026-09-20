from validate_env import (
    check_capital_allocation,
    check_env_file_issues,
    check_mode_consistency,
    check_totp,
    load_env,
)


def test_equals_characters_are_valid_inside_env_value(tmp_path, capsys):
    env_path = tmp_path / ".env"
    env_path.write_text("NEWS_API_KEY=opaque==\n")

    assert load_env(str(env_path))["NEWS_API_KEY"] == "opaque=="
    assert check_env_file_issues(str(env_path)) == []
    assert "WARN" not in capsys.readouterr().out


def test_env_format_checker_still_reports_missing_separator(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("MALFORMED_LINE\n")

    issues = check_env_file_issues(str(env_path))

    assert len(issues) == 1
    assert "no '=' found" in issues[0]


def test_special_safety_checks_return_their_severity():
    assert check_capital_allocation({
        "SWING_CAPITAL_PCT": "0.5",
        "INTRADAY_CAPITAL_PCT": "0.5",
        "SCALPING_CAPITAL_PCT": "0.5",
        "RESERVE_CAPITAL_PCT": "0.5",
    }) == "fail"
    assert check_mode_consistency({
        "PAPER_TRADING": "true",
        "ENABLE_REAL_TRADING": "true",
    }) == "warn"
    assert check_totp({"TOTP_SECRET": "123456"}) == "fail"
