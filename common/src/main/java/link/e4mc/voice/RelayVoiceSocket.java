package link.e4mc.voice;

import de.maxhenkel.voicechat.api.RawUdpPacket;
import de.maxhenkel.voicechat.api.VoicechatSocket;
import link.e4mc.E4mcClient;
import link.e4mc.QuiclimeSession;

import java.io.IOException;
import java.net.BindException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketAddress;
import java.net.SocketException;
import java.util.Arrays;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.BiConsumer;

/**
 * Simple Voice Chat's server socket: players on this computer or network over UDP as usual,
 * and players who joined through the relay over its connection (see {@link RelayVoice}).
 */
public final class RelayVoiceSocket implements VoicechatSocket {
    private record Packet(byte[] getData, SocketAddress getSocketAddress, long getTimestamp) implements RawUdpPacket {}

    // Bounded: voice that can't be processed in time is dropped, as UDP would.
    private final BlockingQueue<RawUdpPacket> received = new LinkedBlockingQueue<>(4096);
    private final BiConsumer<RelayVoice.Player, byte[]> relayed =
            (player, packet) -> received.offer(new Packet(packet, player, System.currentTimeMillis()));
    private DatagramSocket socket;
    private volatile boolean closed;

    @Override
    public void open(int port, String bindAddress) throws Exception {
        InetAddress address = bindAddress.isEmpty() ? null : InetAddress.getByName(bindAddress);
        try {
            socket = new DatagramSocket(port, address);
        } catch (BindException e) {
            // Another world's server on this computer (a background server, say) has the port.
            // Voice chat's own socket would stop a dedicated server here; players are told the
            // port actually in use, so any free one does.
            E4mcClient.LOGGER.warn("Voice chat port {} is in use; using a free port", port);
            socket = new DatagramSocket(0, address);
        }
        Thread reader = new Thread(this::readLocal, "MCPersist voice chat");
        reader.setDaemon(true);
        reader.start();
        RelayVoice.receiver = relayed;
    }

    private void readLocal() {
        byte[] buffer = new byte[4096];
        while (!closed) {
            DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
            try {
                socket.receive(packet);
            } catch (IOException e) {
                continue;
            }
            received.offer(new Packet(Arrays.copyOf(buffer, packet.getLength()), packet.getSocketAddress(),
                    System.currentTimeMillis()));
        }
    }

    @Override
    public RawUdpPacket read() throws Exception {
        while (true) {
            RawUdpPacket packet = received.poll(1, TimeUnit.SECONDS);
            if (packet != null) {
                return packet;
            }
            if (closed) {
                throw new SocketException("Socket closed");
            }
        }
    }

    @Override
    public void send(byte[] data, SocketAddress address) throws Exception {
        if (address instanceof RelayVoice.Player player) {
            QuiclimeSession session = E4mcClient.session;
            if (session != null) {
                session.sendVoice(player, data);
            }
        } else {
            socket.send(new DatagramPacket(data, data.length, address));
        }
    }

    @Override
    public int getLocalPort() {
        return socket.getLocalPort();
    }

    @Override
    public void close() {
        closed = true;
        // Voice chat opens a new socket before closing the old one when its port changes.
        if (RelayVoice.receiver == relayed) {
            RelayVoice.receiver = null;
        }
        socket.close();
    }

    @Override
    public boolean isClosed() {
        return closed;
    }
}
