from datetime import date, timedelta

import charts


def _entry(days_ago: int, score: int) -> dict:
    return {"date": (date.today() - timedelta(days=days_ago)).isoformat(), "score": score}


def test_returns_png_bytes():
    entries = [_entry(10, 4), _entry(5, 6), _entry(1, 7)]
    png = charts.render_mood_chart(entries)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_too_few_points_returns_none():
    assert charts.render_mood_chart([_entry(1, 5)]) is None
    assert charts.render_mood_chart([]) is None


def test_old_and_malformed_entries_ignored():
    entries = [
        _entry(200, 3),                 # outside 90-day window
        {"date": "kaputt", "score": 5}, # unparseable date
        {"date": date.today().isoformat()},  # missing score
        _entry(2, 6),
    ]
    # only one valid point remains → not enough to plot
    assert charts.render_mood_chart(entries) is None
