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

## Joining a world

Shared worlds use a whitelist. Before a friend joins for the first time, the host runs
`/whitelist add <name>`. The background server uses the same whitelist, as it was when the host left:
manage it while you're playing, since changes made on the background server are replaced at the next
handoff.

Anyone can join with an unmodded client: they just type the world's address. Their connection is carried
by the MCPersist relay, which adds a bit of latency.

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

Download the jar for your loader. A world's background server runs the same loader and mods as the
game that hands it off; the first handoff installs that loader's server, which takes up to a minute.

MCPersist can't be installed alongside e4mc.

## Running costs

The relay that carries worlds, voice chat and peer-to-peer connections costs about $6 a month: $5
for the server and $1 for the domain, paid out of pocket. MCPersist is free, and if it's useful to you,
donations help keep it that way:

[![Support MCPersist on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/imivbqcwzjgpvb80cg9t)

## Building

Needs JDK 25. The shared code in `common/` is compiled directly against Minecraft 26.3 (unobfuscated) by
each loader's project: Fabric Loom, ModDevGradle and ForgeGradle.

```
./gradlew build
python test/smoke.py fabric/build/libs/mcpersist-fabric-<version>.jar
```

The smoke test downloads the jar's loader server and runs it with the mod; pass a NeoForge or Forge jar to
test that loader.

## Releasing

Push a tag named after the version, e.g. `git tag v2.0.0-alpha.2 && git push origin v2.0.0-alpha.2`. The
release workflow builds the three jars with that version, attaches them to a GitHub Release, and publishes
one Modrinth version per loader (numbered `<version>+<loader>`).

The Modrinth step runs once the repository has a `MODRINTH_TOKEN` secret (a Modrinth personal access token
with the "Create versions" scope) and a `MODRINTH_PROJECT_ID` variable (the project's ID from its Modrinth
settings). Until then it's skipped.

## Credits and license

Based on e4mc by Skye. MIT licensed; see [LICENSE](LICENSE).

Bundles [iroh-java](https://github.com/vgskye/iroh-java), also by Skye, MIT licensed; see
[its LICENSE](https://github.com/vgskye/iroh-java/blob/main/LICENSE).
