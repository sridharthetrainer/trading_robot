from angel import _is_rate_limited


def test_all_known_angel_rate_limit_responses_are_detected():
    assert _is_rate_limited("Too many requests")
    assert _is_rate_limited({"errorcode": "AB1021"})
    assert _is_rate_limited("access denied because of exceeding access rate")
    assert not _is_rate_limited("invalid symbol token")
