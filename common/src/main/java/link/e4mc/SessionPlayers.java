package link.e4mc;

import net.minecraft.server.players.NameAndId;

import java.util.Set;

/** Implemented on MinecraftServer: everyone who has joined since the world was opened. */
public interface SessionPlayers {
    Set<NameAndId> mcpersist$sessionPlayers();

    /** The world was opened to LAN this session; only then is it handed off on leaving. */
    void mcpersist$markShared();
}
