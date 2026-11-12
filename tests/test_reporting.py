"""Wiedergabe-Meldungen an Jellyfin: wann der Hintergrund-Thread was meldet -
ohne Netzwerk, mit einer Uhr, die der Test selbst weiterstellt."""

import threading
import time

import requests

from jfclite.reporting import PlaybackReporter


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RecordingClient:
    """Merkt sich die Meldungen; mit fail=True antwortet der "Server" nicht."""

    def __init__(self):
        self.calls = []
        self.fail = False
        self.changed = threading.Condition()

    def _record(self, *call):
        with self.changed:
            self.calls.append(call)
            self.changed.notify_all()
        if self.fail:
            raise requests.ConnectionError("Server weg")

    def report_playing(self, track_id, position, paused, session_id):
        self._record("playing", track_id, round(position), paused, session_id)

    def report_progress(self, track_id, position, paused, session_id, event="timeupdate"):
        self._record("progress", track_id, round(position), paused, session_id, event)

    def report_stopped(self, track_id, position, session_id):
        self._record("stopped", track_id, round(position), session_id)

    def wait_for(self, count, timeout=3.0):
        with self.changed:
            self.changed.wait_for(lambda: len(self.calls) >= count, timeout)
        return self.calls


def settle():
    """Dem Thread Zeit geben, um zu zeigen, dass er NICHTS sendet."""
    time.sleep(0.2)


def make():
    client, clock = RecordingClient(), Clock()
    return PlaybackReporter(client, clock=clock), client, clock


def kinds(calls):
    return [c[0] for c in calls]


def test_first_update_reports_the_start():
    reporter, client, clock = make()
    reporter.update("t1", 0.0, False)
    calls = client.wait_for(1)
    assert calls[0][:4] == ("playing", "t1", 0, False)
    assert calls[0][4]  # PlaySessionId gesetzt
    reporter.close()


def test_nothing_is_sent_between_the_regular_progress_reports():
    reporter, client, clock = make()
    reporter.update("t1", 0.0, False)
    client.wait_for(1)
    for second in range(1, 9):
        clock.now += 1
        reporter.update("t1", float(second), False)
    settle()
    assert kinds(client.calls) == ["playing"]
    reporter.close()


def test_progress_is_reported_every_ten_seconds():
    reporter, client, clock = make()
    reporter.update("t1", 0.0, False)
    client.wait_for(1)
    clock.now += 10
    reporter.update("t1", 10.0, False)
    calls = client.wait_for(2)
    assert calls[1][:4] == ("progress", "t1", 10, False)
    assert calls[1][5] == "timeupdate"
    assert calls[1][4] == calls[0][4]  # dieselbe Wiedergabe
    reporter.close()


def test_pause_and_resume_are_reported_at_once():
    reporter, client, clock = make()
    reporter.update("t1", 0.0, False)
    client.wait_for(1)
    clock.now += 2
    reporter.update("t1", 2.0, True)
    calls = client.wait_for(2)
    assert calls[1][0] == "progress" and calls[1][3] is True and calls[1][5] == "pause"
    # Pausiert läuft die erwartete Position nicht weiter: 9 s später kein Spulen
    clock.now += 9
    reporter.update("t1", 2.0, True)
    settle()
    assert len(client.calls) == 2
    reporter.update("t1", 2.0, False)
    calls = client.wait_for(3)
    assert calls[2][3] is False and calls[2][5] == "unpause"
    reporter.close()


def test_seeking_is_reported_at_once():
    reporter, client, clock = make()
    reporter.update("t1", 0.0, False)
    client.wait_for(1)
    clock.now += 1
    reporter.update("t1", 61.0, False)  # 60 s vorgespult
    calls = client.wait_for(2)
    assert calls[1][:3] == ("progress", "t1", 61) and calls[1][5] == "timeupdate"
    reporter.close()


def test_next_track_stops_the_old_one_with_its_last_position():
    reporter, client, clock = make()
    reporter.update("t1", 0.0, False)
    client.wait_for(1)
    clock.now += 5
    reporter.update("t1", 5.0, False)
    settle()
    clock.now += 1
    reporter.update("t2", 0.0, False)
    calls = client.wait_for(3)
    assert calls[1] == ("stopped", "t1", 5, calls[0][4])
    assert calls[2][:3] == ("playing", "t2", 0)
    assert calls[2][4] != calls[0][4]  # neue Wiedergabe, neue PlaySessionId
    reporter.close()


def test_stop_and_close_report_the_end():
    reporter, client, clock = make()
    reporter.update("t1", 3.0, False)
    client.wait_for(1)
    reporter.stop()
    calls = client.wait_for(2)
    assert calls[1][:2] == ("stopped", "t1")
    reporter.update("t2", 0.0, False)
    client.wait_for(3)
    reporter.close()
    calls = client.wait_for(4)
    assert calls[3][:2] == ("stopped", "t2")
    assert not reporter._thread.is_alive()


def test_close_without_playback_sends_nothing():
    reporter, client, clock = make()
    reporter.close()
    assert client.calls == []
    assert not reporter._thread.is_alive()


def test_unreachable_server_is_retried_after_a_pause_only():
    reporter, client, clock = make()
    client.fail = True
    reporter.update("t1", 0.0, False)
    client.wait_for(1)
    for second in range(1, 6):
        clock.now += 1
        reporter.update("t1", float(second), False)
    settle()
    assert len(client.calls) == 1  # kein Dauerfeuer mit Fehlversuchen
    client.fail = False
    clock.now += PlaybackReporter.RETRY_INTERVAL
    reporter.update("t1", 35.0, False)
    calls = client.wait_for(2)
    assert calls[1][:3] == ("playing", "t1", 35)
    reporter.close()


def test_failed_stop_does_not_block_the_next_track():
    reporter, client, clock = make()
    reporter.update("t1", 0.0, False)
    client.wait_for(1)
    client.fail = True
    clock.now += 1
    reporter.update("t2", 0.0, False)
    client.wait_for(2)  # "stopped" für t1 schlägt fehl
    client.fail = False
    clock.now += PlaybackReporter.RETRY_INTERVAL
    reporter.update("t2", 30.0, False)
    calls = client.wait_for(3)
    assert calls[2][:2] == ("playing", "t2")
    reporter.close()
