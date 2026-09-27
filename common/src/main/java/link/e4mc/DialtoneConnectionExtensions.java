package link.e4mc;

import java.net.SocketAddress;

public interface DialtoneConnectionExtensions {
    byte[] e4mc$exportKeyingMaterial(byte[] label, byte[] context, int length);

    /** The address the player dialed, reported as a Dialtone connection's remote address. */
    void e4mc$setDialedAddress(SocketAddress address);
}
