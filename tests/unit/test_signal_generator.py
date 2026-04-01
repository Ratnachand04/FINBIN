from backend.signal.generator import generate_signal


def test_generate_signal_shape():
    result = generate_signal("BTCUSDT")
    assert "symbol" in result
    assert "signal" in result
    assert "confidence" in result
