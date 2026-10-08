package link.mcpersist;

public interface DialtoneConnectionExtensions {
    /** What both ends sign in for Mojang authentication instead of the server hash; see DialtoneChannel. */
    byte[] mcpersist$authDigest();

    /** Whether this is a Dialtone connection; a joining player's reports the dialed address. */
    boolean mcpersist$isDialtone();

    /** Whether this is a player's connection through the relay. */
    boolean mcpersist$isRelayed();

    /** Whether the player answered MCPersist's login query as a client without the mod. */
    boolean mcpersist$withoutMod();

    void mcpersist$markWithoutMod();
}
