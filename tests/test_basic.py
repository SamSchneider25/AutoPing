from ping_core import is_valid_host


def test_validate_valid_ip():
    assert is_valid_host("1.1.1.1") is True

def test_validate_invalid():
    assert is_valid_host("invalid_host!") is False