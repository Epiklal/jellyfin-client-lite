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

> The interface comes in English and German. It follows your system language;
> set `"language"` in the config (or the settings dialog) to override that.

## What it does

- shuffles your entire library, reshuffles when it gets to the end
- media keys work, and so does the KDE/GNOME media widget (via mpv-mpris)
  or the Windows media flyout
- mouse support: click a track to play it, click the timeline to seek, scroll
  on the volume bar
- remembers your library, so after the first run it starts in under a second
  and only reloads when something changed on the server
- can show what you're listening to in Discord (optional)
- tells your server what's playing, like the official apps do: it shows up
  under active devices in the dashboard, play counts and "recently played"
  stay up to date, and plugins like Playback Reporting count it
- shows the cover next to the track name and the lyrics stored on your
  server (synced lyrics highlight the current line); both can be switched off
- search (`/`) across title, artist and album, and a settings dialog (`s`)
- jump to the album of the selected track (`a`), in track order

What it doesn't do: browsing albums or playlists. That's on purpose, there
are bigger clients for that.

## Install

You need Python 3.10+ and mpv. It's built on Linux, Windows works too, and
macOS should (see below).

### Linux

For media keys also install `mpv-mpris` (and `playerctl` if you want to
check that it works). I've tested it on Debian 12 and 13, Ubuntu 24.04 and
26.04, Fedora 44 and Arch.

**Debian, Ubuntu, Linux Mint:** download
[jellyfin-client-lite.deb](https://epiklal.github.io/jellyfin-client-lite/jellyfin-client-lite.deb)
and open it, your software center (Discover, GNOME Software, ...) installs it.
Or in a terminal:

```sh
wget -O /tmp/jellyfin-client-lite.deb https://epiklal.github.io/jellyfin-client-lite/jellyfin-client-lite.deb
sudo apt install /tmp/jellyfin-client-lite.deb
```

That's all, now start "Jellyfin Client Lite" from your app menu or type
`jellyfin-client-lite`. It pulls in mpv and the Python packages, and it adds
its own APT source: new versions arrive with your normal system updates
(Discover, `apt upgrade`, ...) and it shows up in Discover's search. Coming
from 0.3.2 or older? Install it this way once, from then on it updates itself.
To remove it including the APT source: `sudo apt purge jellyfin-client-lite`.

Older versions (like Ubuntu 24.04 or Mint 22) ship a Textual that's too old,
so on those the first start downloads a current one from PyPI into
`~/.local/share/jellyfin-client-lite/venv`, which needs internet once.

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
that it's reachable and shows the server's name) and then for a login:

1. **Quick Connect (recommended):** you get a code and confirm it in a Jellyfin
   that's already logged in (user menu → Quick Connect). No password is stored,
   only an access token that you can revoke on the server. Quick Connect has to
   be enabled on the server (it's a setting in the admin dashboard).
2. **Username and password:** the fallback. The password is only used to log
   in and isn't stored, `config.json` gets an access token here too.

If the address is plain `http://` and looks like it's on the internet, setup
warns you.

To do this again later, run `python run.py --setup` (`.deb`:
`jellyfin-client-lite --setup`, Windows: `jellyfin-client-lite.cmd --setup`).

## Config

You can also write the file by hand, which only works with a password, so the
setup above is usually the better way. Create
`~/.config/jellyfin-client-lite/config.json`
(Windows: `%APPDATA%\jellyfin-client-lite\config.json`):

```json
{
  "server_url": "http://192.168.1.10:8096",
  "username": "your-jellyfin-user",
  "password": "your-password"
}
```

On Linux/macOS, `chmod 600` it, since your password is in there. When running from
source you can also put `config.json` next to `run.py` (it's in
`.gitignore`, so it won't end up in a commit).

The player logs in with these, uses the token it gets back, and logs out
again when you quit.

If JSON isn't your thing: every line except the last one before `}` needs a
comma at the end. The player tells you the line number if one is missing.

## Settings

Press `s` in the player, or edit the config:

```json
"language": "auto",
"autoplay": true,
"start_mode": "shuffle",
"show_cover": true,
"show_lyrics": true,
"report_playback": true
```

- `language`: `"auto"` (system language), `"en"` or `"de"`. Applies from the
  next start.
- `autoplay`: start playing right away. If off, the library loads and waits;
  `Space` or `Enter` on a track starts it.
- `show_cover` / `show_lyrics`: also toggled with `c` / `l`. Lyrics come from
  your Jellyfin server (it has to have them); the cover needs Pillow
  (`pip install pillow`, the `.deb` recommends `python3-pil`) and a terminal
  with true color.
- `report_playback`: tell the server what's playing (*Report playback to
  Jellyfin*). That's a small request when a track starts, ends, is paused or
  seeked, and every 10 seconds in between. Turn it off if you don't want your
  listening in the server's history and statistics.
- `start_mode`: `"shuffle"` or `"sorted"` (by artist, album, track number) for
  the order the list has at startup. `r` always reshuffles. Shuffling spreads
  each artist across the whole list, so they don't come up several times in a
  row just by chance.
- `replaygain`: not in the settings dialog, add it to the config by hand if you
  want it. `"track"` or `"album"` evens out the volume using the ReplayGain tags
  in your files (files without tags play as they are). Off by default. Applies
  from the next start.

Picking a search result plays through the results, and the same goes for an
album opened with `a`. `Esc` brings back the whole library, continuing with the
current track.

## Keys

| Key | |
|---|---|
| `Space` | play / pause |
| `n` / `p` | next / previous track |
| `←` / `→` | seek 10 s |
| `r` | reshuffle |
| `/` | search (live filter; `Enter` jumps to the results, `Esc` closes) |
| `a` | show the album of the selected track (`Esc` goes back) |
| `l` | show / hide lyrics |
| `c` | show / hide cover |
| `s` | settings |
| `+` / `-` | volume (`=` works too) |
| `↑` `↓` `Enter` | pick a track from the list |
| `q` | quit |

Everything at the bottom is clickable as well, including the key hints in
the footer.

## Media keys and the desktop widget

The player doesn't handle media keys itself, mpv does, so how well this works
depends on your platform:

| Platform | What you need | Status |
|---|---|---|
| Linux | the `mpv-mpris` package | tested on the distros listed above |
| Windows | nothing, mpv talks to the Windows media controls | tested on Windows 11, so far only in a VM |
| macOS | nothing, mpv should do it by itself | untested, I don't own a Mac |

Without `mpv-mpris` on Linux, the player still works, but media keys and the
desktop widget do nothing.

On Linux, `playerctl -l` should list `mpv` while the player is running. If not,
check `~/.local/state/jellyfin-client-lite/mpv.log`: every start adds a few
lines there, including whether the plugin was found.
On Windows the logs are in `%LOCALAPPDATA%\jellyfin-client-lite\logs`.

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

To stop sharing, turn off *Share current track on Discord* in the settings
(`s`, the switch only shows up once an Application ID is set). The track
disappears from your profile right away, and the setting is remembered as
`"discord_presence": false`. Your ID stays in the config for when you want it
back.

**Album covers** only work if your Jellyfin is reachable from the internet,
because Discord fetches the image itself:

```json
"public_url": "https://jellyfin.example.com"
```

Keep in mind that anyone who can see your Discord status can then see that
address. Problems end up in `~/.local/state/jellyfin-client-lite/discord.log`.

## Security notes

- After setup, `config.json` only holds an access token, which you can revoke on
  the server. Your password is only there if you wrote it in by hand (see above)
  or set up with an older version, in that case run `--setup` once to replace
  it. Setup writes the file readable only by your user. The access token never
  shows up in a URL: mpv gets it as an HTTP
  header, so it doesn't leak through MPRIS (media widgets, KDE Connect, ...)
  and it's never sent to Discord.
- Control characters in track tags get stripped, so a file with weird tags
  can't mess with your terminal.
- Jellyfin itself serves audio streams and cover images without a login if
  you know an item's ID. You can't list the library that way, but if your
  server is public, you might want to only expose `/Items/*/Images/*` through
  your reverse proxy (enough for Discord covers) and keep the rest behind a
  VPN.
- With `http://` in `server_url`, your password or token goes over the network
  in plain text. Fine on your own wired network, less so on shared wifi.

## Transparent background

The app doesn't paint its own background, so if your terminal has
transparency enabled (Konsole, Kitty, Alacritty, GNOME Terminal all can),
your wallpaper shows through.

## Tests

```sh
pip install -r requirements-dev.txt
pytest
```

They need neither mpv nor a Jellyfin server.

## Building the .deb yourself

```sh
./build-deb.sh
```

The package ends up in `dist/`.

Releasing a new version: put it into `jfclite/__init__.py` and under
`<releases>` in `packaging/io.github.epiklal.jellyfin_client_lite.metainfo.xml`,
commit, then `git tag v<version> && git push --tags`. The release workflow
builds the `.deb`, creates the GitHub release and updates the APT source on
GitHub Pages (signed with the `APT_SIGNING_KEY` secret).

## Credits

The look is borrowed from [jellyfin-tui](https://github.com/dhonus/jellyfin-tui),
which is the one to use if you want a full-featured client.

## License

Apache-2.0, see [LICENSE](LICENSE). You can use, change, share and sell it,
also as part of your own project. The one thing you have to do: keep the
[NOTICE](NOTICE) file, which links back here, with every copy you pass on.

Versions up to 0.3.2 were released under GPL-3.0.
