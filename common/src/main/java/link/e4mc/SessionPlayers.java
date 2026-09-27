package link.e4mc;

import com.mojang.authlib.GameProfile;

import java.util.Set;

/** Implemented on MinecraftServer: everyone who has joined since the world was opened. */
public interface SessionPlayers {
    Set<GameProfile> mcpersist$sessionPlayers();

    /** The world was opened to LAN this session; only then is it handed off on leaving. */
    void mcpersist$markShared();
}
