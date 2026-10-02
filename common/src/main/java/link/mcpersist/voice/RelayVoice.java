package link.mcpersist.voice;

import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.UnknownHostException;
import java.util.Arrays;
import java.util.function.BiConsumer;

/**
 * Simple Voice Chat traffic of players who joined through the relay: it reaches this host as
 * QUIC datagrams, each the player's address (16-byte IPv6, IPv4 mapped, then a 2-byte port)
 * followed by the voice packet, and replies go back the same way.
 *
 * Free of Simple Voice Chat's classes, so the relay session can use it whether or not that
 * mod is installed.
 */
public final class RelayVoice {
    private RelayVoice() {}

    private static final int ADDRESS_LENGTH = 18;

    /** A player whose voice comes through the relay; replies to it go back the same way. */
    public static final class Player extends InetSocketAddress {
        Player(InetAddress address, int port) {
            super(address, port);
        }
    }

    /** The open voice server's socket, which takes packets from relayed players. */
    static volatile BiConsumer<Player, byte[]> receiver;

    /** A datagram from the relay. */
    public static void received(ByteBuf datagram) {
        BiConsumer<Player, byte[]> receiver = RelayVoice.receiver;
        if (receiver == null || datagram.readableBytes() <= ADDRESS_LENGTH) {
            return;
        }
        byte[] ip = new byte[16];
        datagram.readBytes(ip);
        int port = datagram.readUnsignedShort();
        byte[] packet = new byte[datagram.readableBytes()];
        datagram.readBytes(packet);
        try {
            receiver.accept(new Player(InetAddress.getByAddress(ip), port), packet);
        } catch (UnknownHostException e) {
            // Not possible for 16 bytes.
        }
    }

    /** The datagram carrying {@code packet} to {@code player} through the relay. */
    public static ByteBuf frame(Player player, byte[] packet) {
        byte[] ip = player.getAddress().getAddress();
        if (ip.length == 4) {
            byte[] mapped = new byte[16];
            mapped[10] = mapped[11] = (byte) 0xFF;
            System.arraycopy(ip, 0, mapped, 12, 4);
            ip = mapped;
        }
        ByteBuf out = Unpooled.buffer(ADDRESS_LENGTH + packet.length);
        out.writeBytes(ip);
        out.writeShort(player.getPort());
        out.writeBytes(packet);
        return out;
    }

    /** Self-check: {@code java -cp <mod jar and netty> link.mcpersist.voice.RelayVoice}. */
    public static void main(String[] args) throws Exception {
        for (InetAddress ip : new InetAddress[] {InetAddress.getByName("203.0.113.7"), InetAddress.getByName("2001:db8::1")}) {
            Player sent = new Player(ip, 61000);
            byte[][] got = new byte[1][];
            Player[] from = new Player[1];
            receiver = (player, packet) -> {
                from[0] = player;
                got[0] = packet;
            };
            received(frame(sent, new byte[] {(byte) 0xFF, 1, 2}));
            if (!sent.equals(from[0]) || !Arrays.equals(got[0], new byte[] {(byte) 0xFF, 1, 2})) {
                throw new AssertionError(sent + " came back as " + from[0]);
            }
            if (ip instanceof Inet4Address && !(from[0].getAddress() instanceof Inet4Address)) {
                throw new AssertionError("IPv4 address came back as " + from[0].getAddress());
            }
        }
        System.out.println("ok");
    }
}
