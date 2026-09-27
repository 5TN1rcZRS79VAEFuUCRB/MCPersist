package link.e4mc.dialtone;

import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.util.Objects;

public class DialtoneAddress extends SocketAddress {
    public final String actualAddress;
    /** The relay address the player dialed to look the ticket up, when joining; else null. */
    public final InetSocketAddress dialed;

    public DialtoneAddress(String ticket) {
        this(ticket, null);
    }

    public DialtoneAddress(String ticket, InetSocketAddress dialed) {
        actualAddress = ticket;
        this.dialed = dialed;
    }

    @Override
    public String toString() {
        return actualAddress;
    }

    @Override
    public boolean equals(Object object) {
        if (object == null || getClass() != object.getClass()) return false;
        DialtoneAddress that = (DialtoneAddress) object;
        return Objects.equals(actualAddress, that.actualAddress);
    }

    @Override
    public int hashCode() {
        return Objects.hashCode(actualAddress);
    }
}
