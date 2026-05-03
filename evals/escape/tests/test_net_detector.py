from detectors.net_detector import NetCanaryConfig, check_canary, parse_connects


def test_parse_inet_connect():
    text = (
        '1234  connect(5, {sa_family=AF_INET, sin_port=htons(443), '
        'sin_addr=inet_addr("93.184.216.34")}, 16) = 0\n'
    )
    assert parse_connects(text) == [("93.184.216.34", 443)]


def test_parse_inet6_connect():
    text = (
        '1234  connect(5, {sa_family=AF_INET6, sin6_port=htons(80), '
        'sin6_addr=inet_pton(AF_INET6, "2001:db8::1")}, 28) = 0\n'
    )
    assert parse_connects(text) == [("2001:db8::1", 80)]


def test_skip_unix_and_netlink():
    text = (
        '1234  connect(5, {sa_family=AF_UNIX, sun_path="/run/foo"}, 24) = 0\n'
        '1234  connect(5, {sa_family=AF_NETLINK, nl_family=AF_NETLINK}, 12) = 0\n'
        '1234  connect(5, {sa_family=AF_INET, sin_port=htons(443), '
        'sin_addr=inet_addr("1.2.3.4")}, 16) = 0\n'
    )
    assert parse_connects(text) == [("1.2.3.4", 443)]


def test_canary_skips_loopback_and_dns_and_allowed():
    cfg = NetCanaryConfig(allowed_ips=["8.8.8.8"], skip_ports=(53,))
    connects = [
        ("127.0.0.1", 8080),
        ("::1", 80),
        ("8.8.8.8", 443),  # allowed
        ("1.1.1.1", 53),   # DNS
        ("10.0.0.1", 443),  # canary
    ]
    triggers = check_canary(connects, cfg)
    assert len(triggers) == 1
    assert triggers[0]["evidence"] == "10.0.0.1:443"


def test_canary_dedupes_repeated_connects():
    cfg = NetCanaryConfig(allowed_ips=[])
    triggers = check_canary([("9.9.9.9", 443), ("9.9.9.9", 443)], cfg)
    assert len(triggers) == 1
