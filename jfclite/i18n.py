"""Oberflächentexte: im Code stehen sie auf Deutsch, t() liefert sie bei Bedarf
englisch. Ein Text ohne Übersetzung bleibt deutsch (tests/test_i18n.py meldet
das). Weitere Sprachen: noch ein Dict wie EN und in set_language() freischalten.

Tastenkürzel und Auswahllisten in app.py werden schon beim Import übersetzt -
set_language() muss also vorher laufen, siehe run.py.
"""

from __future__ import annotations

import locale
import os
import sys
from typing import Optional


def _detect() -> str:
    """Deutsch bei deutscher Systemsprache, sonst Englisch."""
    names = [os.environ.get(v, "") for v in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")]
    if sys.platform == "win32":  # dort stehen diese Variablen meist nicht
        try:
            names.append(locale.getlocale()[0] or "")
        except ValueError:
            pass
    for name in names:
        name = name.lower()
        if name and not name.startswith(("c.", "posix")) and name != "c":
            return "de" if name.startswith(("de", "german")) else "en"
    return "en"


_language = _detect()


def set_language(value: Optional[str]) -> None:
    """Wert von "language" aus der Config: "de", "en", sonst automatisch."""
    global _language
    _language = value if value in ("de", "en") else _detect()


def t(text: str, **values: object) -> str:
    if _language == "en":
        text = EN.get(text, text)
    return text.format(**values) if values else text


EN = {
    # Einstellungen
    "Zufällig gemischt": "Shuffled",
    "Sortiert (Interpret, Album)": "Sorted (artist, album)",
    "Automatisch": "Automatic",
    "Abbrechen": "Cancel",
    "Einstellungen": "Settings",
    "Autoplay (beim Start sofort abspielen)": "Autoplay (start playing right away)",
    "Cover neben dem Titel anzeigen": "Show cover next to the title",
    "Songtext anzeigen": "Show lyrics",
    "Aktuellen Titel in Discord teilen": "Share current track on Discord",
    "Wiedergabe an Jellyfin melden": "Report playback to Jellyfin",
    "Beim Start: Reihenfolge": "Order at startup",
    "Sprache": "Language",
    "Speichern": "Save",
    "Gespeichert (Autoplay, Reihenfolge und Sprache gelten ab dem nächsten Start).": (
        "Saved (autoplay, order and language apply from the next start)."
    ),
    "Speichern fehlgeschlagen: {error}": "Saving failed: {error}",
    # Tastenkürzel
    "Nächster Titel": "Next track",
    "Vorheriger Titel": "Previous track",
    "Neu mischen": "Reshuffle",
    "Suche": "Search",
    "Songtext": "Lyrics",
    "Suche schließen": "Close search",
    "Lauter": "Louder",
    "Leiser": "Quieter",
    "Beenden": "Quit",
    # Hauptansicht
    "Suche in Titel, Interpret, Album ...": "Search title, artist, album ...",
    "Enter = zur Liste, Esc = schließen": "Enter = to the list, Esc = close",
    "Klick oder Enter = abspielen": "Click or Enter = play",
    "Lade Bibliothek ...": "Loading library ...",
    "⇄ Mischen": "⇄ Shuffle",
    "Titel": "Title",
    "Interpret": "Artist",
    "Dauer": "Length",
    "Für Cover fehlt Pillow:  pip install pillow": "Covers need Pillow:  pip install pillow",
    "Kann bei großen Bibliotheken oder über VPN/Tunnel einen Moment dauern.": (
        "This can take a moment with big libraries or over a VPN/tunnel."
    ),
    "⚠ Bibliothek konnte nicht geladen werden": "⚠ Couldn't load the library",
    "[r] zum erneuten Versuch": "[r] to try again",
    "Keine Titel in der Jellyfin-Bibliothek gefunden.": "No tracks found in the Jellyfin library.",
    "{n} Titel geladen - Leertaste oder Enter zum Abspielen": (
        "{n} tracks loaded - Space or Enter to play"
    ),
    "⚠ mpv ließ sich nicht starten": "⚠ Couldn't start mpv",
    "Leertaste für neuen Versuch": "Space to try again",
    "⚠ mpv stürzt immer wieder ab": "⚠ mpv keeps crashing",
    "Details stehen in {path}  —  Leertaste für neuen Versuch": (
        "Details are in {path}  —  Space to try again"
    ),
    "mpv wurde unerwartet beendet (Exit-Code {code}) und neu gestartet.": (
        "mpv quit unexpectedly (exit code {code}) and was restarted."
    ),
    "Bibliothek aktualisiert: {n} Titel": "Library updated: {n} tracks",
    "Suche: {n} Treffer": "Search: {n} hits",
    "Suchtreffer ({n})": "Search hits ({n})",
    "Alle Titel (zufällige Reihenfolge)": "All tracks (shuffled)",
    "Alle Titel (sortiert)": "All tracks (sorted)",
    "Dieser Titel gehört zu keinem Album.": "This track isn't part of an album.",
    "⚠ Server liefert keine Titel": "⚠ The server isn't delivering any tracks",
    "{n} Titel hintereinander ließen sich nicht laden. "
    "Wiedergabe angehalten - Leertaste für neuen Versuch.": (
        "{n} tracks in a row failed to load. Playback stopped - Space to try again."
    ),
    "Lade Songtext ...": "Loading lyrics ...",
    "Kein Songtext vorhanden.": "No lyrics available.",
    # Einrichtung
    "Serveradresse (z.B. 192.168.1.10:8096)": "Server address (e.g. 192.168.1.10:8096)",
    "  Keine Verbindung zu {url}: {error}": "  Can't connect to {url}: {error}",
    "  Adresse und Port prüfen (Jellyfin nutzt meist :8096).\n": (
        "  Check the address and port (Jellyfin usually uses :8096).\n"
    ),
    "  Verbunden mit „{name}“ (Jellyfin {version})\n": "  Connected to \"{name}\" (Jellyfin {version})\n",
    "  Achtung: http:// ist unverschlüsselt, Passwort und Token wären mitlesbar.": (
        "  Careful: http:// isn't encrypted, your password and token could be read along the way."
    ),
    "  Über das Internet besser https:// verwenden.\n": "  Over the internet, better use https://.\n",
    "Benutzername": "Username",
    "Passwort (bleibt beim Tippen unsichtbar): ": "Password (stays invisible while typing): ",
    "  Anmeldung nicht möglich: {error}\n": "  Couldn't log in: {error}\n",
    "  Alternative: Anmeldung mit Passwort (Auswahl 2).\n": "  Alternative: log in with a password (choice 2).\n",
    "\n  Dein Code:  {code}\n": "\n  Your code:  {code}\n",
    "  In Jellyfin (Browser oder App) anmelden, dann": "  Log in to Jellyfin (browser or app), then",
    "  Benutzermenü → Quick Connect → Code eingeben. Abbruch mit Strg+C.": (
        "  user menu → Quick Connect → enter the code. Ctrl+C to cancel."
    ),
    "  Der Code ist abgelaufen.\n": "  The code has expired.\n",
    "\n  Abgebrochen.\n": "\n  Cancelled.\n",
    "  Quick Connect fehlgeschlagen: {error}\n": "  Quick Connect failed: {error}\n",
    "Optional: in Discord (auch Equibop, Vesktop) anzeigen, was läuft.": (
        "Optional: show what's playing in Discord (Equibop and Vesktop too)."
    ),
    "Dafür unter https://discord.com/developers/applications eine Anwendung": (
        "For that, create an application at https://discord.com/developers/applications"
    ),
    "anlegen (ihr Name wird angezeigt) und hier ihre Application-ID eingeben.": (
        "(its name is what gets shown) and enter its Application ID here."
    ),
    "Enter = behalten, - = entfernen": "Enter = keep, - = remove",
    "Enter = keine": "Enter = none",
    "  Die ID besteht nur aus Ziffern (17-20 Stellen).": "  The ID consists of digits only (17-20 of them).",
    "Einrichtung von jellyfin-client-lite\n": "Setting up jellyfin-client-lite\n",
    "Wie willst du dich anmelden?": "How do you want to log in?",
    "  1  Quick Connect (empfohlen: Code in einem schon angemeldeten Jellyfin bestätigen,\n"
    "     es wird kein Passwort gespeichert)": (
        "  1  Quick Connect (recommended: confirm a code in a Jellyfin that's already\n"
        "     logged in, no password gets stored)"
    ),
    "  2  Benutzername und Passwort (gespeichert wird auch hier nur ein Token)": (
        "  2  Username and password (here too, only a token gets stored)"
    ),
    "Auswahl": "Choice",
    "Angemeldet.\n": "Logged in.\n",
    "Fertig. Die Einstellungen (Autoplay usw.) öffnest du im Player mit [s].\n": (
        "Done. Press [s] in the player to open the settings (autoplay etc.).\n"
    ),
    # Fehler vom Server
    "Anmeldung fehlgeschlagen: Benutzername oder Passwort falsch.": (
        "Login failed: wrong username or password."
    ),
    "Die gespeicherte Anmeldung ist abgelaufen oder wurde widerrufen.": (
        "The stored login has expired or was revoked."
    ),
    "Quick Connect ist auf diesem Server nicht aktiviert.": "Quick Connect isn't enabled on this server.",
    "Nicht angemeldet - login() wurde nicht aufgerufen.": "Not logged in - login() wasn't called.",
    "Unbekannt": "Unknown",
    "Keine Verbindung zu mpv, Details stehen in {path}": "Can't connect to mpv, details are in {path}",
    # Start (run.py)
    "{path} ist nicht als UTF-8 gespeichert. Im Editor beim": "{path} isn't saved as UTF-8. Pick the UTF-8",
    "Speichern die Codierung UTF-8 wählen.": "encoding when saving it in your editor.",
    "Fehler in {path}, Zeile {line}, Spalte {column}: {error}": "Error in {path}, line {line}, column {column}: {error}",
    r'In Pfaden \ verdoppeln oder / nehmen: "C:\\mpv\\mpv.exe" oder "C:/mpv/mpv.exe"': (
        r'In paths, double the \ or use /: "C:\\mpv\\mpv.exe" or "C:/mpv/mpv.exe"'
    ),
    "Häufigste Ursache: am Ende der Zeile DAVOR fehlt ein Komma.": (
        "Most common cause: a missing comma at the end of the line BEFORE it."
    ),
    "Jede Zeile außer der letzten vor } muss mit , enden, z.B.:": (
        "Every line except the last one before } has to end with a comma, e.g.:"
    ),
    "{path} enthält kein JSON-Objekt. Datei korrigieren oder löschen, beim\n"
    "nächsten Start läuft dann die Einrichtung.": (
        "{path} doesn't contain a JSON object. Fix or delete the file, setup then\n"
        "runs on the next start."
    ),
    "In {path} fehlt: {fields}": "Missing from {path}: {fields}",
    "Neu einrichten mit:  {command}": "To set it up again:  {command}",
    "Am einfachsten einmal im Terminal starten: die Einrichtung meldet dich per\n"
    "Quick Connect an, ganz ohne Passwort in der Datei. Von Hand geht es auch:": (
        "The easiest way is to start it once in a terminal: setup logs you in with\n"
        "Quick Connect, with no password in the file. By hand it works like this:"
    ),
    "{path} ist leer (vielleicht nicht gespeichert?).": "{path} is empty (maybe not saved?).",
    "Keine Konfiguration gefunden. Zum Anlegen das hier eingeben, Notepad\n"
    "fragt dann, ob es die Datei erstellen soll (Ja):\n": (
        "No config found. To create one, enter this, Notepad then asks\n"
        "whether to create the file (Yes):\n"
    ),
    "Hinein kommt, mit deinen Daten:\n": "This goes in, with your details:\n",
    "Keine Konfiguration gefunden. Bitte {path} anlegen, z.B.:": "No config found. Please create {path}, e.g.:",
    "Serveradresse, Benutzername, Passwort eintragen": "enter server address, username, password",
    '"mpv_path" in der Konfiguration zeigt auf {path},\naber dort liegt kein mpv.': (
        '"mpv_path" in the config points to {path},\nbut there is no mpv there.'
    ),
    "mpv fehlt. Am einfachsten im Terminal:\n": "mpv is missing. The easiest way, in a terminal:\n",
    "Oder von https://mpv.io/installation/ die Windows-Version von shinchiro\n"
    "holen (mpv-x86_64-...7z) und entpacken nach {path}\n"
    "Beides findet der Player von selbst. Liegt mpv woanders, in der\n"
    'config.json "mpv_path": "C:/Pfad/zu/mpv.exe" eintragen.': (
        "Or get the Windows build by shinchiro from https://mpv.io/installation/\n"
        "(mpv-x86_64-...7z) and extract it to {path}\n"
        "The player finds both by itself. If mpv is somewhere else, add\n"
        '"mpv_path": "C:/path/to/mpv.exe" to config.json.'
    ),
    "mpv fehlt. Installieren mit:\n": "mpv is missing. Install it with:\n",
    "mpv fehlt. Installieren, z.B.:\n": "mpv is missing. Install it, e.g.:\n",
    "Es fehlen Python-Pakete ({packages}). Starte den Player mit\n"
    "jellyfin-client-lite.cmd (Doppelklick), das richtet sie beim ersten Mal ein.": (
        "Python packages are missing ({packages}). Start the player with\n"
        "jellyfin-client-lite.cmd (double-click), it sets them up on the first run."
    ),
    "Es fehlen Python-Pakete ({packages}). Einmalig in {path}:": (
        "Python packages are missing ({packages}). Once, in {path}:"
    ),
    "Danach so starten:\n": "Then start it like this:\n",
    "Gespeichert in {path}\n": "Saved to {path}\n",
    "Jellyfin unter {url} ist nicht erreichbar.": "Can't reach Jellyfin at {url}.",
    "Stimmt die Adresse in der Konfiguration, und läuft der Server?": (
        "Is the address in the config right, and is the server running?"
    ),
    "Anmeldung bei Jellyfin fehlgeschlagen: {error}": "Logging in to Jellyfin failed: {error}",
    "\nBeendet.": "\nQuit.",
}
