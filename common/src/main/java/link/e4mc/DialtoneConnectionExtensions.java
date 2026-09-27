package link.e4mc;

import java.net.SocketAddress;

public interface DialtoneConnectionExtensions {
    byte[] e4mc$exportKeyingMaterial(byte[] label, byte[] context, int length);

    /** Whether this is a Dialtone connection (its remote address is the dialed one, not a ticket). */
    boolean e4mc$isDialtone();

    /** The address the player dialed, reported as a Dialtone connection's remote address. */
    void e4mc$setDialedAddress(SocketAddress address);
}
