# MCPersist

e4mc, but your world keeps running after you leave.

MCPersist v2 is a Fabric mod based on [e4mc](https://github.com/vgskye/e4mc-minecraft-architectury). Like
e4mc, it puts your LAN world on the internet at an address your friends can join with an unmodded client.
It also lets you mark a world as persistent. When you leave that world, or close the game, it keeps running
as a headless server in the background on your machine, and it keeps the same address every time.

> **Status: early development.** Right now this is a renamed e4mc fork built for Minecraft 26.3. The persistence features are tracked in the
> [v2 spec](https://github.com/5TN1rcZRS79VAEFuUCRB/persistent-e4mc/issues/1).

Looking for MCPersist v1, the Windows app? It lives on the [`v1` branch](../../tree/v1).

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

## Credits and license

Based on e4mc by Skye. MIT licensed; see [LICENSE](LICENSE).
