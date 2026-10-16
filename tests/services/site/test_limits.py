from jendhub.services.site.limits import RateLimit


def test_a_client_gets_its_burst_and_then_waits() -> None:
    limit = RateLimit(per_minute=60, burst=2)
    assert limit.take("a", now=0.0) == 0.0
    assert limit.take("a", now=0.0) == 0.0
    assert limit.take("a", now=0.0) == 1.0
    assert limit.take("b", now=0.0) == 0.0


def test_a_client_gets_tokens_back_with_time() -> None:
    limit = RateLimit(per_minute=60, burst=1)
    assert limit.take("a", now=0.0) == 0.0
    assert limit.take("a", now=0.5) == 0.5
    assert limit.take("a", now=1.5) == 0.0
