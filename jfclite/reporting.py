"""Meldet Jellyfin, was gerade läuft - wie es die offiziellen Apps tun.

Ohne diese Meldungen sieht der Server nur Stream-Abrufe: kein Eintrag unter
"Aktive Geräte" im Dashboard, keine Wiedergabezahl und kein "zuletzt
gespielt" (darauf bauen Vorschläge und "Meistgespielt" auf), nichts in der
Statistik des Playback-Reporting-Plugins.

Läuft in einem eigenen Hintergrund-Thread, damit ein langsamer oder nicht
erreichbarer Server die TUI nie bremst. Gesendet wird beim Titelstart, beim
Titelende, bei Pause/Fortsetzen und Spulen und sonst alle 10 s ein kurzer
Zwischenstand (so viel wie bei den offiziellen Apps; ohne den räumt Jellyfin
die Wiedergabe nach einigen Minuten als abgebrochen ab). Ist der Server weg,
wird erst nach 30 s wieder versucht - kein Dauerfeuer mit Fehlversuchen.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Optional, Tuple


class PlaybackReporter:
    PROGRESS_INTERVAL = 10.0  # Sekunden zwischen zwei Zwischenständen
    RETRY_INTERVAL = 30.0  # nach einem Fehler so lange Pause
    SEEK_TOLERANCE = 3.0  # Abweichung (s) von der erwarteten Position = gespult

    def __init__(self, client, clock: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._clock = clock  # austauschbar für die Tests
        self._cond = threading.Condition()
        # (Titel-ID, Position, pausiert, Zeitpunkt der Messung) oder None = nichts läuft
        self._want: Optional[Tuple[str, float, bool, float]] = None
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="jellyfin-report", daemon=True)
        self._thread.start()

    def update(self, track_id: str, position: float, paused: bool) -> None:
        """Wird jede Sekunde aufgerufen; der Thread entscheidet, ob etwas rausgeht."""
        with self._cond:
            self._want = (track_id, position, paused, self._clock())
            self._cond.notify()

    def stop(self) -> None:
        """Es läuft nichts mehr (angehalten nach Fehlern, Melden ausgeschaltet)."""
        with self._cond:
            self._want = None
            self._cond.notify()

    def close(self) -> None:
        """Beim Beenden: laufenden Titel noch als beendet melden. Muss vor
        client.logout() kommen - danach ist der Token ungültig."""
        with self._cond:
            self._want = None
            self._closing = True
            self._cond.notify()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        sent_id: Optional[str] = None  # Titel, dessen Start der Server kennt
        session = ""
        sent_paused = False
        sent_pos = 0.0  # Position beim letzten Senden ...
        sent_at = 0.0  # ... und wann das war
        last_pos = 0.0  # zuletzt gemessene Position von sent_id (für "beendet")
        next_try = 0.0
        while True:
            with self._cond:
                if not self._closing:
                    self._cond.wait(timeout=1.0)
                want, closing = self._want, self._closing
            now = self._clock()
            try:
                if sent_id and (want is None or want[0] != sent_id):
                    # Vorher austragen: schlägt das fehl, soll es den nächsten
                    # Titel nicht blockieren
                    old, sent_id = sent_id, None
                    self._client.report_stopped(old, last_pos, session)
                if want and now >= next_try:
                    track_id, pos, paused, measured = want
                    pos_now = pos + (0.0 if paused else now - measured)
                    if sent_id is None:
                        session = uuid.uuid4().hex
                        self._client.report_playing(track_id, pos_now, paused, session)
                        sent_id = track_id
                    else:
                        expected = sent_pos + (0.0 if sent_paused else now - sent_at)
                        if paused != sent_paused:
                            event = "pause" if paused else "unpause"
                        elif (
                            abs(pos_now - expected) > self.SEEK_TOLERANCE
                            or now - sent_at >= self.PROGRESS_INTERVAL
                        ):
                            event = "timeupdate"
                        else:
                            event = ""
                        if event:
                            self._client.report_progress(track_id, pos_now, paused, session, event)
                        else:
                            last_pos = pos_now
                            continue
                    sent_paused, sent_pos, sent_at = paused, pos_now, now
                    last_pos = pos_now
            except Exception:
                # Server nicht erreichbar, Token abgelaufen, ... - die Musik
                # läuft weiter, gemeldet wird später wieder
                next_try = now + self.RETRY_INTERVAL
            if closing:
                return
