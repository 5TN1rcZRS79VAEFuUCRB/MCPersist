![MCPersist: share your world, and it keeps running after you leave](https://raw.githubusercontent.com/5TN1rcZRS79VAEFuUCRB/MCPersist/main/docs/images/banner.png)

Play with your friends in your singleplayer world, from anywhere, **without port forwarding**. When you log off, the world keeps going.

MCPersist gives your world a permanent address. Open it to LAN, send your friends the address once, and they can join whenever it's running, even after you've quit the game.

> **Made with AI:** most of MCPersist's code and text is written with AI (Claude); I decide what it does and test it in-game with friends. Prefer non-AI software? [e4mc](https://modrinth.com/mod/e4mc), which this is forked from, is a good choice when you don't need the world to keep running.

## Why MCPersist?

| | e4mc | MCPersist |
|---|---|---|
| Friends join from anywhere | ✅ | ✅ |
| Friends need the mod | No | No |
| Same address every time | ❌ New address each session | ✅ Permanent |
| World keeps running when you leave | ❌ | ✅ In the background on your PC |
| Only people you allow can join | ❌ | ✅ Whitelist on by default |

## How to use it

1. **Share:** open a world and click **Open to LAN**. The address appears in chat; click it to copy it.
2. **Let friends in:** run `/whitelist add <name>` for each friend before they join for the first time.
3. **Keep it running:** turn on **Keep running after I leave** when you create a world, or under **Edit** in the world list.
4. **Come back:** a world running in the background shows **Running in the background** in the world list. Click it to join, or use **Stop Background Server** to stop it.

Your friends join by adding the address as a server in Multiplayer. If they install MCPersist too, they connect to you **peer-to-peer**, which is usually faster than going through the relay.

## Good to know

- **Alpha:** built for **Minecraft 26.3** on **Fabric** (with Fabric API), **NeoForge** or **Forge**. Back up your worlds.
- **Only the host needs the mod.** Players can join with an unmodded game.
- **Works on dedicated servers too.** Put the mod in the server's mods folder and the server gets a permanent address, written to its log. The server's own whitelist setting decides who can join.
- **Simple Voice Chat works** through the relay, with no extra ports to open.
- **Relays in Chicago and Frankfurt.** MCPersist hosts through whichever is closer to you, automatically. [Relay status](https://stats.uptimerobot.com/zCMpp0FH4u)
- **The background server runs on your computer.** It uses memory and CPU while it runs and only works while your computer is on. It starts again when you log in, until you stop it or open the world yourself.
- **Using the background server accepts the [Minecraft EULA](https://aka.ms/MinecraftEULA)** for that server.

## Network and privacy

- Players connect through the MCPersist relays at mcpersist.com, or peer-to-peer when both sides have the mod. The relays forward game traffic, which Minecraft encrypts. They store your world's address name, but not your world or your chat.
- MCPersist downloads no files. Its native libraries (for the relay connection and peer-to-peer) are inside the mod jar.
- The background server runs from your game's own installation, the same Minecraft and mod loader files your launcher installed, with a copy of the mods in your game's mods folder.
- When you share a world, MCPersist checks mcpersist.com for a newer version and says so in chat if there is one (`checkForUpdates = false` in the config turns this off). It never downloads updates.
- MCPersist doesn't collect usage data or analytics.

## Running costs

The relays cost about $11 a month (a server in Chicago and one in Frankfurt, $5 each, plus $1 for the domain), paid out of pocket. MCPersist is free, and if it's useful to you, donations help keep it that way:

[![Support MCPersist on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/imivbqcwzjgpvb80cg9t)

## Credits

MCPersist is a fork of [e4mc](https://modrinth.com/mod/e4mc) by Skye, under the MIT license, and bundles her [iroh-java](https://github.com/vgskye/iroh-java). It isn't affiliated with or endorsed by e4mc.

[Source code](https://github.com/5TN1rcZRS79VAEFuUCRB/MCPersist) · [Report an issue](https://github.com/5TN1rcZRS79VAEFuUCRB/MCPersist/issues)
