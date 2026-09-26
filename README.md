# MCPersist

e4mc, but your world keeps running after you leave.

MCPersist v2 is a Fabric mod based on [e4mc](https://github.com/vgskye/e4mc-minecraft-architectury). Like
e4mc, it puts your LAN world on the internet at an address your friends can join with an unmodded client.
It also lets you mark a world as persistent. When you leave that world, or close the game, it keeps running
as a headless server in the background on your machine, and it keeps the same address every time.

> **Status: alpha.** Built for Minecraft 26.3. Expect rough edges.

## Joining a world

Anyone can join with an unmodded client: they just type the world's address. Their connection is carried
by the MCPersist relay, which adds a bit of latency.

**Players should install MCPersist too if they can.** With the mod, a player connects to the world
peer-to-peer instead of through the relay, which is usually faster and more stable. This works the same
whether the host is playing or the world is running in the background.

MCPersist v1, the Windows app, is discontinued and its relay is shut down. Its code is kept on the
[`v1` branch](../../tree/v1).

## Requirements

- Fabric, on the latest Minecraft release (26.x)
- Fabric API

MCPersist can't be installed alongside e4mc.

## Building

Needs JDK 25. The mod is compiled directly against Minecraft 26.3 (unobfuscated) with Fabric Loom.

```
./gradlew :fabric:build
python test/smoke.py fabric/build/libs/mcpersist-fabric-<version>.jar
```

The smoke test downloads a Fabric server and runs it with the mod.

## Releasing

Push a tag named after the version, e.g. `git tag v2.0.0-alpha.2 && git push origin v2.0.0-alpha.2`. The
release workflow builds the jar with that version, attaches it to a GitHub Release (a pre-release for
alpha, beta and rc versions), and publishes it to Modrinth.

The Modrinth step runs once the repository has a `MODRINTH_TOKEN` secret (a Modrinth personal access token
with the "Create versions" scope) and a `MODRINTH_PROJECT_ID` variable (the project's ID from its Modrinth
settings). Until then it's skipped.

## Credits and license

Based on e4mc by Skye. MIT licensed; see [LICENSE](LICENSE).

Bundles [iroh-java](https://github.com/vgskye/iroh-java), also by Skye, which the author has said to treat
as MIT licensed (its repository doesn't include a license file yet).
