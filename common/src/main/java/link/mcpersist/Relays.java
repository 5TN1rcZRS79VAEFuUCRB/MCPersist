package link.mcpersist;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.concurrent.CompletableFuture;

/**
 * The MCPersist relays, one per region. A world's address lives on one relay (its region is
 * part of the address), so a persistent world stays on the relay it was first hosted through.
 * Persistent worlds keep their address only on a relay that stores world keys, which e4mc's don't.
 */
public final class Relays {
    /** The first is where persistent worlds from before there were regions live. */
    static final String[] ALL = {"relay.mcpersist.com", "relay.eu.mcpersist.com"};
    /** Each relay's iroh relay, whose port answers TCP: connecting to it measures the ping. */
    private static final int PING_PORT = 8443;

    private Relays() {}

    /** The relay to host {@code worldDir} through; {@code override} is the config's, empty for none. */
    public static String forWorld(Path worldDir, String override) {
        if (!override.isEmpty()) {
            return override;
        }
        if (!WorldPersistence.isPersistent(worldDir)) {
            return nearest();
        }
        String relay = WorldPersistence.relay(worldDir);
        if (relay == null) {
            return ALL[0];
        }
        if (relay.isEmpty()) {
            relay = nearest();
            try {
                WorldPersistence.setRelay(worldDir, relay);
            } catch (IOException e) {
                MCPersist.LOGGER.error("Failed to save the world's relay; it'll be chosen again next time", e);
            }
        }
        return relay;
    }

    static String nearest() {
        var pings = Arrays.stream(ALL).map(relay -> CompletableFuture.supplyAsync(() -> ping(relay))).toList();
        long[] ms = pings.stream().mapToLong(CompletableFuture::join).toArray();
        int best = 0;
        for (int i = 0; i < ALL.length; i++) {
            MCPersist.LOGGER.info("Ping to {}: {}", ALL[i], ms[i] == Long.MAX_VALUE ? "unreachable" : ms[i] + " ms");
            if (ms[i] < ms[best]) {
                best = i;
            }
        }
        return ALL[best];
    }

    // ponytail: best of three TCP connects; a real latency probe if relays ever sit close together.
    private static long ping(String relay) {
        long best = Long.MAX_VALUE;
        try {
            InetAddress address = InetAddress.getByName(relay);
            for (int i = 0; i < 3; i++) {
                long start = System.nanoTime();
                try (Socket socket = new Socket()) {
                    socket.connect(new InetSocketAddress(address, PING_PORT), 2000);
                }
                best = Math.min(best, (System.nanoTime() - start) / 1_000_000);
            }
        } catch (IOException ignored) {
        }
        return best;
    }

    /** Self-check of the per-world rules, needing no network: java -cp ... link.mcpersist.Relays */
    public static void main(String[] args) throws Exception {
        Path world = Files.createTempDirectory("relays");
        assert forWorld(world, "relay.example").equals("relay.example");
        WorldPersistence.setPersistent(world, true);
        assert WorldPersistence.relay(world).isEmpty() : "a new key's relay is chosen on first use";
        WorldPersistence.setRelay(world, "relay.eu.mcpersist.com");
        assert forWorld(world, "").equals("relay.eu.mcpersist.com");
        // A persistent world from before regions: key but no relay.
        Files.writeString(world.resolve(WorldPersistence.FILE), "persistent=true\nkey=abc\n");
        assert forWorld(world, "").equals(ALL[0]);
        System.out.println("ok");
    }
}
