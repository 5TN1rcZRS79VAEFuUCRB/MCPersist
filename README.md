<p align="center">
  <img src="common/src/main/resources/mcpersist_icon.png" alt="MCPersist icon" width="180">
</p>

<h1 align="center">MCPersist</h1>

<p align="center"><i>e4mc, but your world keeps running after you leave.</i></p>

MCPersist v2 is a Fabric, NeoForge and Forge mod based on [e4mc](https://github.com/vgskye/e4mc-minecraft-architectury). Like
e4mc, it puts your LAN world on the internet at an address your friends can join with an unmodded client.
It also lets you mark a world as persistent. When you leave that world, or close the game, it keeps running
as a headless server in the background on your machine, and it keeps the same address every time.

> **Status: alpha.** Built for Minecraft 26.3. Expect rough edges.

> **AI disclosure.** MCPersist is written with heavy use of AI (Claude, which shows up in the contributors
> list). I decide what it does and test it in-game with friends, but most of the code and text is
> AI-written. Back up your worlds. If you'd rather not use AI-assisted software, that's fair:
> [e4mc](https://github.com/vgskye/e4mc-minecraft-architectury), which this is forked from and which doesn't
> accept AI contributions, is a good choice when you don't need the world to keep running.

<p align="center">
  <a href="https://www.youtube.com/watch?v=hEOFd0jpw-4">
    <img src="https://img.youtube.com/vi/hEOFd0jpw-4/maxresdefault.jpg" alt="Watch the MCPersist walkthrough on YouTube" width="640">
  </a>
  <br>
  <i>Five-minute walkthrough: install, share your world, leave, and your friends keep playing.</i>
</p>

## Joining a world

Shared worlds use a whitelist. Before a friend joins for the first time, the host runs
`/whitelist add <name>`. The background server uses the same whitelist, as it was when the host left:
manage it while you're playing, since changes made on the background server are replaced at the next
handoff.

Anyone can join with an unmodded client: they just type the world's address. Their connection is carried
by the MCPersist relay, which adds a bit of latency. There are relays in Chicago and Frankfurt, and the host's
game picks the closer one; if a world can't be reached, check the
[relay status page](https://stats.uptimerobot.com/zCMpp0FH4u).

**Players should install MCPersist too if they can.** With the mod, a player connects to the world
peer-to-peer instead of through the relay, which is usually faster and more stable. This works the same
whether the host is playing or the world is running in the background.

MCPersist v1, the Windows app, is discontinued and its relay is shut down. Its code is kept on the
[`v1` branch](../../tree/v1).

## Requirements

- Minecraft 26.3, with one of:
  - Fabric, plus Fabric API
  - NeoForge
  - Forge

Download the jar for your loader from [GitHub Releases](../../releases). (The Modrinth and CurseForge
listings aren't public yet.) A world's background server runs the same loader and mods as the
game that hands it off, run from the game's own installation: MCPersist downloads nothing.

MCPersist can't be installed alongside e4mc.

## Dedicated servers

MCPersist works on a dedicated server too: put the jar (and Fabric API, on Fabric) in the server's `mods`
folder. When the server starts it connects to the relay and writes its address to the log
(`Domain assigned: <address>`). The address stays the same across restarts: the first start gives the world
a key, stored in `mcpersist.properties` in the world folder. Keep that file private, since it holds the
address. Players with the mod connect peer-to-peer, as with a shared world.

The server's own `white-list` setting in `server.properties` decides who can join. MCPersist doesn't turn it
on, so a server without a whitelist is open to anyone who has the address. To keep a server off the relay,
set `hostEnabled = false` in `config/mcpersist/mcpersist.toml`. With `requireMod = true`, only players who have
MCPersist can join; anyone else is turned away with a link to the mod.

## Official server

Want to see MCPersist in action? Join **official.mcpersist.com** (Minecraft 26.3 with MCPersist and Simple Voice Chat installed).
It's a public survival server hosted from a home PC through the MCPersist relay, with no port forwarding.
Claim land with diamonds, team up with friends, and raid other teams in declared wars. The one rule is no
cheating.

## Running costs

The relays that carry worlds, voice chat and peer-to-peer connections cost about $11 a month: a server
in Chicago and one in Frankfurt at $5 each, plus $1 for the domain, paid out of pocket. MCPersist is free, and if it's useful to you,
donations help keep it that way:

[![Support MCPersist on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/imivbqcwzjgpvb80cg9t)

## Building

Needs JDK 25. The shared code in `common/` is compiled directly against Minecraft 26.3 (unobfuscated) by
each loader's project: Fabric Loom, ModDevGradle and ForgeGradle.

```
./gradlew build
python test/smoke.py fabric/build/libs/mcpersist-fabric-<version>.jar
```

The smoke test downloads the jar's loader server and a client installation (for the handoff to run its server
from) and runs them with the mod; pass a NeoForge or Forge jar to test that loader.

## Releasing

Push a tag named after the version, e.g. `git tag v2.0.0-alpha.2 && git push origin v2.0.0-alpha.2`. The
release workflow builds the three jars with that version, attaches them to a GitHub Release, and publishes
one Modrinth version per loader (numbered `<version>+<loader>`) and one CurseForge file per loader.

The Modrinth step runs once the repository has a `MODRINTH_TOKEN` secret (a Modrinth personal access token
with the "Create versions" scope) and a `MODRINTH_PROJECT_ID` variable (the project's ID from its Modrinth
settings). Until then it's skipped. Likewise the CurseForge step needs a `CURSEFORGE_TOKEN` secret (an API
token from CurseForge's account settings) and a `CURSEFORGE_PROJECT_ID` variable (the project's number).

## Credits and license

Licensed under GPL-3.0-or-later; see [LICENSE](LICENSE).

Based on e4mc by Skye, which is MIT licensed. Its original notice is kept in
[LICENSE-MIT-e4mc](LICENSE-MIT-e4mc).

Bundles [iroh-java](https://github.com/vgskye/iroh-java), also by Skye, MIT licensed; see
[its LICENSE](https://github.com/vgskye/iroh-java/blob/main/LICENSE).
