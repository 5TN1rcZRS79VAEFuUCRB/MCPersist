# MCPersist

A tool for turning a singleplayer Minecraft world into a real standalone dedicated
server, running independently of the game client, with a **persistent** join address
(via a relay - see [`relay/`](relay/)) that stays the same across restarts. Inspired
by [e4mc](https://modrinth.com/mod/e4mc), whose tunneled address is tied to the game
client's own session and resets whenever you close it.

The server keeps running after you close Minecraft. With autostart enabled, it also
comes back automatically after a reboot (as long as your PC is on).

## Quickstart

1. Download `MCPersist-windows.zip` from [Releases](../../releases) and extract it.
2. Run `MCPersist.exe` inside the extracted folder. Click **Set Up New World**, pick
   your Minecraft instance folder, then either select one of your existing
   singleplayer worlds to promote, generate a brand-new one from scratch, or switch
   to a server you've already set up before (instant, no re-download), and follow the
   wizard - the right Java version downloads automatically if you don't already have
   it, no separate install needed.
3. Click **Start**. The join address shown is what you give your friends.

Set up more than one world over time? Every server you've made stays around - open
**Set Up New World** again and pick **Switch to a Previous Server** to jump between
them instantly, without re-downloading anything.

No Python install needed for this path - everything's bundled into the folder.

**A note on antivirus/SmartScreen warnings**: MCPersist isn't code-signed (that costs
money and this is a free hobby project), so some antivirus engines and Windows
SmartScreen may flag or warn about the binary the first time - this is extremely
common for small, unsigned open-source tools and not evidence of anything malicious.
The source is fully here in this repo if you'd rather verify or build it yourself; see
[Building from source](#building-from-source).

## Updating

MCPersist checks GitHub for a newer release once when it starts up. If one's out, a
banner with an **Update Now** button shows up on the status screen - click it and it
downloads, installs, and restarts itself, no manual re-download needed. This only
applies to the packaged `.exe`; running from source, use `git pull` instead (`run.bat
check-update` will tell you if there's anything new either way).

## Whitelist & adding friends

Setup auto-detects the world's owner from the save file and whitelists just them -
the server starts **closed by default, not open**.

**Leave the whitelist on.** Once the tunnel is up, your server is reachable at a
public address. Port-scanning bots and random players routinely find and probe open
Minecraft servers on the internet - this isn't a hypothetical risk. With online-mode
on but no whitelist, literally any real Minecraft account can join and grief the
world. Turning the whitelist off, even briefly, means anyone who happens to scan your
address in that window gets in.

The main status screen always shows whether the whitelist is currently ON or OFF
(with a warning if it's off) and who's currently on it, so it's not something you
have to remember to check or dig through a log for.

**To add or remove a friend**, run either through RCON/the server console, or
in-game by an op:

```
whitelist add <their exact Minecraft username>
whitelist remove <username>
whitelist list
```

Case-sensitive, needs their real account name. To make someone an op too:
`op <username>`, same way. All of these take effect immediately, no restart needed -
or from the CLI, `run.bat whitelist-add <username>` does the same as the first one
(works whether the server's running or not).

If the owner couldn't be auto-detected during setup (an ambiguous or already-shared
world - setup says so explicitly when this happens), the whitelist is left off and
flagged with a warning - turn it on yourself as soon as possible: set
`white-list=true` in `server.properties`, add players to `whitelist.json` (server
stopped) or via `whitelist add`/`whitelist on` over RCON (server running).

## Memory & performance

RAM and view/simulation distance are auto-sized from your PC's specs by default (a
background-persistent server shares the machine with whatever else you're doing, so
this favors running smoothly alongside other things over maxing out the hardware) -
visible and editable in the **Memory** and **Performance** sections of the status
screen, each with an **Auto** toggle. Turn it off to set a value yourself; either way,
changes take effect on the next Start/Restart, not retroactively on an already-running
server (the status screen tells you which, once you hit Save).

## The tunnel

Starting the server connects to the relay baked into `config.json`'s defaults with
**no setup required** - it registers automatically and gets back a random address
(e.g. `quiet-badger.mcpersist.com`). That address is ephemeral: it can change on the
next start.

For a **fixed, memorable address that never changes** - the actual point of this
project for anyone who cares about persistence - get a subdomain + token from
whoever runs the relay you're using, then run `configure-relay` (CLI) or use the
Configure Relay option (only relevant if you're managing a reserved address; most
people never need it). That reserves the subdomain permanently - every future start
uses it instead of a random one.

### Running your own relay

The relay (the piece that gives out addresses like `*.mcpersist.com`) is just as
open source as the client - see [`relay/README.md`](relay/README.md) for deploying
your own on a VPS you control. Point any MCPersist client at it with
`configure-relay`; nothing about the client is tied to the default one.

## Known limitations

- Windows + Fabric/vanilla only (no Forge/NeoForge yet - detected and flagged with a
  clear warning if your world uses one, not silently mis-set-up).
- The server only runs while your PC is on and awake.
- Fabric mod support copies your client `mods/` folder as a starting point. Client-only
  mods (rendering, HUD, etc.) can crash a dedicated server - if startup fails, check
  `servers/<world>/logs/server.out.log` and remove the offending mod(s) from
  `servers/<world>/mods/`.
- Java is handled automatically: if `java_path` in `config.json` is still the default,
  setup/start download a matching portable Temurin JRE into `bin/` themselves (like
  Prism/MultiMC do) rather than requiring a system-wide install. Set `java_path`
  explicitly if you'd rather point at your own Java install instead.
- If your Fabric modpack already bundles e4mc (likely, if you're switching to
  MCPersist from it), setup automatically skips copying it into the server's `mods/`
  folder - e4mc is built against different Minecraft mappings server-side and crashes
  the whole server the moment a player joins if left in. You don't need it anyway;
  MCPersist replaces what it does.
- The default relay is open to anyone running this tool (auto-registration, no token
  needed) - the only abuse control is a per-IP connection cap. No bandwidth limits or
  TLS on the control/data channels yet. Fine at small scale; revisit if a given relay
  gets real traffic. See `relay/README.md`.
- No world backups. If the PC or the world save is lost, it's gone - worth setting up
  your own backup of `servers/<world>/world` if that matters to you.

## How it works

```
mcpersist/
  cli.py               the `run.bat ...` command-line interface
  tray_app.py           the GUI's entry point (window + system tray icon)
  gui_pages.py           the GUI's screens (status, setup wizard)
  actions.py            start/stop/status logic - shared by the CLI and GUI
  setup_flow.py          setup/configure-relay logic - shared by the CLI and GUI
  world.py               reads world saves: version/loader/owner detection
  server_vanilla.py     downloads a matching vanilla server jar
  server_fabric.py       downloads a matching Fabric server jar + mods
  java_manager.py         auto-downloads a matching portable Java (Temurin) if needed
  mojang.py               UUID -> username lookups (for whitelist/op)
  tunnel_relay.py        launches the tunnel client as a detached process
  tunnel_relay_run.py     the tunnel client itself - talks to the relay
  process_manager.py     detached-process/PID-file management
  rcon.py                  minimal RCON client (graceful server stop, admin commands)
  config.py               config.json load/save, RAM/view-distance auto-sizing
  autostart.py            start-on-login (Task Scheduler or Startup-folder fallback)
  version.py               the app's own version, compared against GitHub releases
  update_checker.py        checks for/applies updates to the packaged .exe

relay/                  runs on a VPS you control - see relay/README.md
  relay_server.py        the relay: routes connections by subdomain, pipes bytes
  mc_handshake.py         parses just enough of the Minecraft protocol to read the
                          hostname a connecting client typed
  users.py / auto_assignments.py   reserved and auto-assigned subdomain registries
  admin_cli.py             operator CLI for issuing subdomain+token pairs
```

- `setup` copies the chosen world, downloads a matching server, and writes
  `server.properties`/`eula.txt`/whitelist.
- `start` launches the server and the tunnel client as **detached** background
  processes (not tied to the terminal/GUI that started them), so closing either
  doesn't stop them.
- The tunnel client connects outbound to the relay, which peeks at each incoming
  Minecraft handshake to route by subdomain and bridges bytes between the player and
  your server. Reconnects automatically (with backoff) if the relay restarts or the
  connection drops.
- `stop` sends `stop` to the server over RCON for a clean shutdown, then stops the
  tunnel.
- `autostart install` registers a Windows Scheduled Task that runs `start` at login -
  or, if Task Scheduler's "on logon" trigger is blocked (some locked-down/managed
  Windows images restrict it for standard users), falls back to a Startup-folder
  entry instead, which needs no special privileges.

## Building from source

For contributors, or anyone who'd rather not run a downloaded binary:

```bash
git clone <this repo>
cd MCPersist
pip install -r requirements.txt
python -m mcpersist setup
python -m mcpersist start      # or: python main_gui.py, for the GUI
```

To build the binary yourself:

```bash
pip install pyinstaller
pyinstaller --name MCPersist --onedir --windowed --noupx main_gui.py
```

(`--onedir`, not `--onefile` - a self-extracting single exe is exactly the pattern
that gets flagged as a false positive by some antivirus engines; see the note above.
The build lands in `dist/MCPersist/` - zip that folder up if you want to distribute
it, same as the release builds do.)

**Troubleshooting a locked-down/managed Windows machine**: if `python.org`'s installer
fails with `0x80070003` from Windows Installer, or `pip install` fails building a
package from source with no compiler available, your machine may be blocking
MSI-based installs and/or lack a C toolchain (common on corporate/managed images).
Workarounds: use Python's [embeddable
package](https://www.python.org/downloads/windows/) (a portable zip, no installer)
instead of the normal installer, and prefer packages that ship prebuilt wheels for
your Python version over ones that need to compile from source.

**A Windows/Java quirk worth knowing**: on a machine with a long username, Java's
networking can fail to start at all with `Unable to establish loopback connection` /
`Invalid argument: connect`. Root cause: Java opens an internal `AF_UNIX` socket
under `%TEMP%`, and that path is capped at ~108 bytes - a long
`C:\Users\<name>\AppData\Local\Temp\...` can push it over the limit. MCPersist works
around this by launching the server with `TEMP`/`TMP` pointed at a short path instead
(see `SHORT_TMP_DIR` in `mcpersist/process_manager.py`) - if you ever see that error
in `server.out.log`, this is the first thing to check.

## License

MIT - see [LICENSE](LICENSE).
