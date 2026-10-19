# jellyfin-client-lite

A small terminal music player for Jellyfin. You start it, it shuffles your
whole music library, and that's pretty much it.

I wanted something that just plays my music in the background without a
browser tab or an Electron app eating RAM. The actual audio is handled by
[mpv](https://mpv.io), the Python part only talks to it over a socket, and
streams go out untouched (no transcoding), so neither your PC nor your server
has much to do.

On my machine (Debian 13, KDE, ~3000 tracks) it sits at around 60 MB for the
interface plus ~70 MB for mpv, and about 0.3 % of one CPU core while playing.

> The interface is in German at the moment, since that's what I use. The
> config and everything in this README apply either way.

## What it does

- shuffles your entire library, reshuffles when it gets to the end
- media keys work, and so does the KDE/GNOME media widget (via mpv-mpris)
  or the Windows media flyout
- mouse support: click a track to play it, click the timeline to seek, scroll
  on the volume bar
- remembers your library, so after the first run it starts in under a second
  and only reloads when something changed on the server
- can show what you're listening to in Discord (optional)

- shows the album cover next to the track name and the lyrics stored on your
  server (synced lyrics highlight the current line); both can be switched off
- search (`/`) across title, artist and album, and a settings dialog (`s`)
- evens out loudness between tracks (ReplayGain), since shuffling a whole
  library jumps between very differently mastered albums
- can pick up where you left off: same order, same track, same position
- if the server goes away (restart, VPN drops), it pauses and retries instead
  of skipping through your library

What it doesn't do: browsing albums or playlists. That's on purpose, there
are bigger clients for that.

## Install

You need Python 3.10+ and mpv. It's built on Linux, Windows works too, and
macOS should (see below).

### Linux

For media keys also install `mpv-mpris` (and `playerctl` if you want to
check that it works). I've tested it on Debian 12 and 13, Ubuntu 24.04 and
26.04, Fedora 44 and Arch.

**Debian 13+ or Ubuntu 26.04+:** grab the `.deb` from the
[releases page](https://github.com/Epiklal/jellyfin-client-lite/releases)
and run

```sh
sudo apt install ./jellyfin-client-lite_*_all.deb
```

This pulls in mpv and the Python packages and adds "Jellyfin Client Lite"
to your app menu. Run it from there or type `jellyfin-client-lite`.

Older Debian/Ubuntu versions (like 24.04) ship a Textual that's too old for
the `.deb`, so use the source install below.

**Anything else / from source:**

```sh
git clone https://github.com/Epiklal/jellyfin-client-lite
cd jellyfin-client-lite
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python run.py
```

### macOS

```sh
brew install mpv python
```

Then the same steps as "from source" above. The mpv.app from mpv.io works
too, as long as it's in Applications.

I don't own a Mac, so this one is untested. It runs the exact same code as
on Linux though, so I'd expect it to just work. If you try it, let me know
how it went.

### Windows

1. Download this repo (green *Code* button → *Download ZIP*) and extract it.
2. Install Python from [python.org](https://www.python.org/downloads/).
3. Install mpv. The quickest way is `winget install shinchiro.mpv` in a
   terminal. You can also download the Windows build from
   [mpv.io](https://mpv.io/installation/) (the shinchiro one,
   `mpv-x86_64-….7z`) and extract it into the player's folder. The player
   finds it either way.
4. Double-click `jellyfin-client-lite.cmd`. The first start takes a minute,
   it sets up its own Python environment in `.venv`. Windows might warn you
   because the file came from the internet: *More info* → *Run anyway*.

If something is missing (config, mpv), it tells you what and how to fix it.
mpv somewhere else entirely? Add `"mpv_path": "D:/tools/mpv/mpv.exe"` to
the config. Use forward slashes, JSON doesn't like single backslashes.

Windows Terminal (the default on Windows 11) handles the mouse and colors
best.

Tested on Windows 11 with mpv from winget, so far only in a VM.

## First start

If there's no config yet, the player asks for your server address (it checks
that it's reachable and shows the server's name) and then lets you log in
either with username and password or with **Quick Connect**: you get a code
and confirm it in a Jellyfin that's already logged in (user menu → Quick
Connect). Either way only an access token is stored, never your password.
Run `python run.py --setup` (`jellyfin-client-lite --setup` with the `.deb`)
to do this again later. If the stored login stops working (e.g. you removed
the device in the Jellyfin dashboard), the player asks you to log in again.

## Config

You can also write the file by hand. Create `~/.config/jellyfin-client-lite/config.json`
(Windows: `%APPDATA%\jellyfin-client-lite\config.json`):

```json
{
  "server_url": "http://192.168.1.10:8096",
  "username": "your-jellyfin-user",
  "password": "your-password"
}
```

On Linux/macOS, `chmod 600` it until the first start. When running from
source you can also put `config.json` next to `run.py` (it's in
`.gitignore`, so it won't end up in a commit).

On the first start the player logs in with these and then replaces the
password in the file with the access token it got back (plus a `device_id`),
so your password doesn't stay on disk. The token stays valid until you remove
the device in the Jellyfin dashboard.

If JSON isn't your thing: every line except the last one before `}` needs a
comma at the end. The player tells you the line number if one is missing.

## Settings

Press `s` in the player, or edit the config:

```json
"autoplay": true,
"start_mode": "shuffle",
"show_cover": true,
"show_lyrics": true,
"replaygain": "track"
```

- `autoplay`: start playing right away. If off, the library loads and waits;
  `Space` or `Enter` on a track starts it.
- `show_cover` / `show_lyrics`: also toggled with `c` / `l`. Lyrics come from
  your Jellyfin server (it has to have them); the cover needs Pillow
  (`pip install pillow`, the `.deb` recommends `python3-pil`) and a terminal
  with true color.
- `start_mode`: `"shuffle"`, `"sorted"` (by album artist, album, track
  number) or `"resume"` (the order, track and position from last time, with
  tracks that were added in the meantime mixed into the part you haven't
  heard yet). `r` always reshuffles.
- `replaygain`: `"track"`, `"album"` or `"no"`. Uses the ReplayGain tags in
  your files to even out the volume; files without tags play as they are.

Picking a search result plays through the results; closing the search
brings back the whole library, continuing with the current track. The search
shows at most 500 results, type a bit more to narrow it down.

## Keys

| Key | |
|---|---|
| `Space` | play / pause |
| `n` / `p` | next / previous track |
| `←` / `→` | seek 10 s |
| `r` | reshuffle |
| `/` | search (live filter; `Enter` jumps to the results, `Esc` closes) |
| `l` | show / hide lyrics |
| `c` | show / hide cover |
| `s` | settings |
| `g` | jump back to the playing track in the list |
| `+` / `-` | volume (`=` works too) |
| `↑` `↓` `Enter` | pick a track from the list |
| `q` | quit |

Everything at the bottom is clickable as well, including the key hints in
the footer.

While you scroll or move through the list, the cursor stays where you put it
instead of jumping to each new track; `g` brings you back.

## Media keys and the desktop widget

On Linux: install `mpv-mpris` and restart the player. `playerctl -l` should
list `mpv` while it's running. If not, check
`~/.local/state/jellyfin-client-lite/mpv.log`: every start adds a few lines
there, including whether the plugin was found.

On Windows there's nothing to install: mpv shows up in the Windows media
controls by itself, so the media keys and the media flyout just work.
Logs are in `%LOCALAPPDATA%\jellyfin-client-lite\logs`.

On macOS mpv should do the same, but I haven't been able to check that.

## Discord

If Discord (the official app, or Vesktop/Equibop with arRPC turned on) runs
on the same machine, the player can show the current track on your profile.
It only sends an update when the track changes or you pause/seek, so it
costs basically nothing.

1. Create an application at <https://discord.com/developers/applications>.
   Its name is what people see ("Listening to *name*").
2. Copy the Application ID into your config:

   ```json
   "discord_client_id": "123456789012345678"
   ```

3. In Discord, turn on *Activity Privacy → Share your detected activities
   with others*.

**Album covers** only work if your Jellyfin is reachable from the internet,
because Discord fetches the image itself:

```json
"public_url": "https://jellyfin.example.com"
```

Keep in mind that anyone who can see your Discord status can then see that
address. Problems end up in `~/.local/state/jellyfin-client-lite/discord.log`.

## Security notes

- Your password is never stored, only an access token (in `config.json`,
  readable only by you). The token never shows up in a URL: mpv gets it as
  an HTTP header, so it doesn't leak through MPRIS (media widgets, KDE
  Connect, ...) and it's never sent to Discord.
- Logins use the `Authorization` header. The old `X-Emby-*` headers are off
  by default from Jellyfin 10.12 on, so older versions of this player stop
  working there.
- Control characters in track tags get stripped, so a file with weird tags
  can't mess with your terminal.
- Jellyfin itself serves audio streams and cover images without a login if
  you know an item's ID. You can't list the library that way, but if your
  server is public, you might want to only expose `/Items/*/Images/*` through
  your reverse proxy (enough for Discord covers) and keep the rest behind a
  VPN.
- With `http://` in `server_url`, your password (once, when logging in) and
  the token go over the network in plain text. Fine on your own wired
  network, less so on shared wifi.

## Transparent background

The app doesn't paint its own background, so if your terminal has
transparency enabled (Konsole, Kitty, Alacritty, GNOME Terminal all can),
your wallpaper shows through.

## Building the .deb yourself

```sh
./build-deb.sh
```

The package ends up in `dist/`.

## Credits

The look is borrowed from [jellyfin-tui](https://github.com/dhonus/jellyfin-tui),
which is the one to use if you want a full-featured client.

## License

GPL-3.0, see [LICENSE](LICENSE).
