from datetime import UTC, datetime

from crawler.firestore import decode_fields, encode_fields, to_value


def test_value_roundtrip():
    data = {
        "s": "text",
        "i": 3,
        "f": 0.5,
        "b": False,
        "n": None,
        "t": datetime(2026, 10, 22, 9, 31, 53, 554000, tzinfo=UTC),
        "a": ["x", 1],
        "m": {"k": "v"},
    }
    assert decode_fields(encode_fields(data)) == data


def test_timestamp_encoding_is_utc_z():
    assert to_value(datetime(2026, 1, 1, tzinfo=UTC)) == {"timestampValue": "2026-01-01T00:00:00.000000Z"}
    assert to_value(True) == {"booleanValue": True}
    assert to_value(7) == {"integerValue": "7"}


def test_nanosecond_timestamps_decode():
    assert decode_fields({"t": {"timestampValue": "2026-10-22T09:58:17.801541123Z"}})["t"] == datetime(
        2026, 10, 22, 9, 58, 17, 801541, tzinfo=UTC
    )
