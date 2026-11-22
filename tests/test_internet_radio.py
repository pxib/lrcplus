from contextlib import nullcontext
from unittest.mock import Mock

from PySide6.QtTest import QTest

from core import radio_browser
from core.radio_browser import PAGE_SIZE, Station, StationPage
from core.radio_history import LIMIT, clear_station_history, load_station_history, remember_station
from ui.main_window import Player


def test_search_uses_resolved_urls_filters_and_mirror_fallback(monkeypatch):
    valid = {
        "name": " Local  Radio ", "url": "https://radio.example/playlist.pls",
        "url_resolved": "https://radio.example/stream", "tags": "rock, blues", "bitrate": 128,
    }
    malformed = {"name": "Invalid", "url_resolved": "file:///tmp/recording"}
    response = Mock()
    response.json.return_value = [malformed, valid] + [valid] * (PAGE_SIZE - 1)
    get = Mock(side_effect=[radio_browser.requests.Timeout(), nullcontext(response)])
    monkeypatch.setattr(radio_browser.requests, "get", get)

    page = radio_browser.search_stations("Local", "Canada", "rock", PAGE_SIZE)

    assert len(page.stations) == PAGE_SIZE - 1
    assert page.stations[0] == Station("Local Radio", valid["url_resolved"], tags="rock, blues", bitrate=128)
    assert page.has_next
    first, second = get.call_args_list
    assert first.args[0] != second.args[0]
    assert second.kwargs["params"] == {
        "order": "clickcount", "reverse": "true", "hidebroken": "true", "limit": PAGE_SIZE + 1,
        "offset": PAGE_SIZE, "name": "Local", "country": "Canada", "tag": "rock",
    }
    assert second.kwargs["headers"]["User-Agent"].startswith("LRCPlus/")


def test_station_history_retains_only_the_latest_stations_and_can_be_cleared():
    stations = [Station(f"Station {index}", f"https://radio.example/{index}")
                for index in range(LIMIT + 1)]
    for station in stations:
        remember_station(station)
    assert [station for station, _ in load_station_history()] == stations[:0:-1]
    clear_station_history()
    assert load_station_history() == []


def test_radio_menu_searches_and_plays_selected_station(qapp, monkeypatch):
    station = Station("Example Jazz", "https://example.org/live", "Japan", "jazz", "MP3", 128)
    search = Mock(return_value=StationPage((station,), True))
    monkeypatch.setattr("ui.internet_radio.search_stations", search)
    player = Player()
    play = Mock()
    monkeypatch.setattr(player, "load_stream", play)
    try:
        action = next(action for action in player.open_menu.actions() if "Network Playback" in action.text())
        assert action.shortcut().toString() == "Ctrl+Shift+U"
        action.trigger()
        dialog = player._network_playback_dialog
        action.trigger()
        assert player._network_playback_dialog is dialog
        assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == "Direct URL"
        search.assert_not_called()
        dialog.url.setText("https://example.org/other-stream")
        radio = dialog.radio
        dialog.tabs.setCurrentWidget(radio)

        radio.name.setText("Example")
        radio.country.setText("Japan")
        radio.tag.setText("jazz")
        radio.search_button.click()
        for _ in range(80):
            if radio.play_button.isEnabled():
                break
            QTest.qWait(10)
        assert radio.results.topLevelItem(0).text(0) == station.name
        search.assert_called_with("Example", "Japan", "jazz", 0)
        radio.play_button.click()
        play.assert_called_once_with(station.url, station=station)
        assert dialog.isVisible()

        radio.next_button.click()
        for _ in range(80):
            if radio.previous_button.isEnabled():
                break
            QTest.qWait(10)
        search.assert_called_with("Example", "Japan", "jazz", PAGE_SIZE)
        assert radio.previous_button.isEnabled()
        call_count = search.call_count
        dialog.tabs.setCurrentIndex(0)
        assert dialog.url.text() == "https://example.org/other-stream"
        dialog.tabs.setCurrentWidget(radio)
        assert search.call_count == call_count
        dialog.close()
        assert player._network_playback_dialog is None
    finally:
        player.close()
        player.deleteLater()
