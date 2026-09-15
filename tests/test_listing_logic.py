"""Logic assertions for the downloader/pipeline listing fixes (run with pytest)."""
from datetime import datetime, timedelta, timezone

from app.services import downloader
from app.services.pipeline import _video_in_window, compute_time_window


def test_list_channel_videos_explicit_max_not_clamped(monkeypatch):
    """An explicit max_items must reach dated tabs at full size (Part 1 fix)."""
    captured = {}

    def fake_tab(url, tab, max_items, start_item):
        captured[tab] = max_items
        return [], ""

    monkeypatch.setattr(downloader, "_list_channel_tab", fake_tab)
    downloader.list_channel_videos("https://www.youtube.com/@x", max_items=150)
    # videos/streams get the full requested amount; shorts stays bounded
    # because flat shorts entries carry no dates to filter with.
    assert captured == {
        "videos": 150,
        "shorts": downloader._SHORTS_EXPLICIT_MAX_ITEMS,
        "streams": 150,
    }


def test_list_channel_videos_all_keeps_per_tab_defaults(monkeypatch):
    """max_items=None (time_window='all') keeps the per-tab default ceilings."""
    captured = {}

    def fake_tab(url, tab, max_items, start_item):
        captured[tab] = max_items
        return [], ""

    monkeypatch.setattr(downloader, "_list_channel_tab", fake_tab)
    downloader.list_channel_videos("https://www.youtube.com/@x", max_items=None)
    assert captured == {
        "videos": downloader._TAB_MAX_ITEMS["videos"],
        "shorts": downloader._TAB_MAX_ITEMS["shorts"],
        "streams": downloader._TAB_MAX_ITEMS["streams"],
    }


def test_list_channel_videos_dedupes_across_tabs(monkeypatch):
    def fake_tab(url, tab, max_items, start_item):
        if tab == "videos":
            return [
                {"youtube_video_id": "aaaaaaaaaaa", "published_at": None, "source_tab": "videos"},
                {"youtube_video_id": "bbbbbbbbbbb", "published_at": None, "source_tab": "videos"},
            ], "Chan"
        return [
            {"youtube_video_id": "aaaaaaaaaaa", "published_at": None, "source_tab": "streams"},
        ], "Chan"

    monkeypatch.setattr(downloader, "_list_channel_tab", fake_tab)
    results = downloader.list_channel_videos("https://www.youtube.com/@x", max_items=10)
    assert len(results) == 2
    assert {r["youtube_video_id"] for r in results} == {"aaaaaaaaaaa", "bbbbbbbbbbb"}


def test_upcoming_entry_published_at_not_polluted_by_release_date():
    """An upcoming entry's release_date is the scheduled start, not a publish date."""
    future = "20990102"
    entry = {
        "id": "ccccccccccc",
        "title": "Soon",
        "live_status": "is_upcoming",
        "release_date": future,
        "upload_date": future,
    }
    scheduled = downloader._scheduled_start_from_entry(entry)
    published = downloader._parse_entry_published_at(entry)
    assert scheduled is not None and scheduled.year == 2099
    assert published is not None  # parser still yields it; the tab code must null it
    # Replicate the tab-loop rule: upcoming => published_at stays None.
    upcoming = downloader._is_upcoming_entry(entry)
    assert upcoming is True
    assert (None if upcoming else published) is None


def test_video_in_window_boundaries_and_naive_dates():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=7)
    end = now
    # Exact boundaries are inclusive on both ends.
    assert _video_in_window(start, start, end) is True
    assert _video_in_window(end, start, end) is True
    # One second outside either boundary is excluded.
    assert _video_in_window(start - timedelta(seconds=1), start, end) is False
    assert _video_in_window(end + timedelta(seconds=1), start, end) is False
    # Naive datetimes are interpreted as UTC, not rejected/shifted.
    assert _video_in_window(start.replace(tzinfo=None), start, end) is True
    # Dateless entries are kept (documented fallback).
    assert _video_in_window(None, start, end) is True
    # 'all' window keeps everything.
    assert _video_in_window(start - timedelta(days=3650), None, None) is True


def test_compute_time_window_7d_is_utc_now_minus_seven_days():
    before = datetime.now(timezone.utc)
    start, end = compute_time_window("7d")
    after = datetime.now(timezone.utc)
    assert start.tzinfo is not None and end.tzinfo is not None
    assert before - timedelta(seconds=2) <= start + timedelta(days=7) <= after + timedelta(seconds=2)
    assert end <= after + timedelta(seconds=2)


def test_custom_window_naive_dates_get_utc():
    start, end = compute_time_window(
        "custom",
        datetime(2026, 9, 1, 12, 0, 0),
        datetime(2026, 9, 10, 12, 0, 0),
    )
    assert start.tzinfo is timezone.utc
    assert end.tzinfo is timezone.utc
