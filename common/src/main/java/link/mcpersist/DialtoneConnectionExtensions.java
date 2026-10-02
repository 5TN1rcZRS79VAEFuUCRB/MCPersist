package link.mcpersist;

public interface DialtoneConnectionExtensions {
    /** What both ends sign in for Mojang authentication instead of the server hash; see DialtoneChannel. */
    byte[] mcpersist$authDigest();

    /** Whether this is a Dialtone connection; a joining player's reports the dialed address. */
    boolean mcpersist$isDialtone();
}
