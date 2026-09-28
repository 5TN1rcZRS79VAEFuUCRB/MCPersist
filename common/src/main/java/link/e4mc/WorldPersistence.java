package link.e4mc;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.Properties;

/**
 * A world's persistence settings, stored in the world folder so they travel with it
 * (backups and copies keep the address). The key is a secret: the relay gives the same
 * key the same address every time. The relay is where that address lives: see {@link Relays}.
 */
public final class WorldPersistence {
    static final String FILE = "mcpersist.properties";
    private static final String PERSISTENT = "persistent";
    private static final String KEY = "key";
    private static final String RELAY = "relay";
    private static final SecureRandom RANDOM = new SecureRandom();

    private WorldPersistence() {}

    public static boolean isPersistent(Path worldDir) {
        return Boolean.parseBoolean(read(worldDir).getProperty(PERSISTENT));
    }

    /** The key to present to the relay, or null for a world that isn't persistent. */
    public static String keyFor(Path worldDir) {
        Properties props = read(worldDir);
        return Boolean.parseBoolean(props.getProperty(PERSISTENT)) ? props.getProperty(KEY) : null;
    }

    public static boolean hasKey(Path worldDir) {
        return read(worldDir).getProperty(KEY) != null;
    }

    /** Turning persistence off keeps the key, so turning it back on keeps the address. */
    public static void setPersistent(Path worldDir, boolean persistent) throws IOException {
        Properties props = load(worldDir);
        props.setProperty(PERSISTENT, Boolean.toString(persistent));
        if (persistent && props.getProperty(KEY) == null) {
            newKey(props);
        }
        store(worldDir, props);
    }

    /** Gives the world a new key, and so a new address. */
    public static void resetKey(Path worldDir) throws IOException {
        Properties props = load(worldDir);
        newKey(props);
        store(worldDir, props);
    }

    /**
     * The relay holding the world's address: null for a key from before there were regions
     * (it lives on the first relay), empty for a new key whose relay isn't chosen yet.
     */
    static String relay(Path worldDir) {
        return read(worldDir).getProperty(RELAY);
    }

    static void setRelay(Path worldDir, String relay) throws IOException {
        Properties props = load(worldDir);
        props.setProperty(RELAY, relay);
        store(worldDir, props);
    }

    // A new key is a new address, so it's placed on whichever relay is nearest when it's first used.
    private static void newKey(Properties props) {
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        props.setProperty(KEY, Base64.getUrlEncoder().withoutPadding().encodeToString(bytes));
        props.setProperty(RELAY, "");
    }

    /** For readers: an unreadable file counts as "not persistent". */
    private static Properties read(Path worldDir) {
        try {
            return load(worldDir);
        } catch (IOException e) {
            E4mcClient.LOGGER.error("Failed to read {}", worldDir.resolve(FILE), e);
            return new Properties();
        }
    }

    /** For writers: an unreadable file is an error, so the key is never overwritten blind. */
    private static Properties load(Path worldDir) throws IOException {
        Properties props = new Properties();
        try (InputStream in = Files.newInputStream(worldDir.resolve(FILE))) {
            props.load(in);
        } catch (NoSuchFileException ignored) {
        }
        return props;
    }

    // Losing the key loses the world's address, so never leave a half-written file.
    private static void store(Path worldDir, Properties props) throws IOException {
        Path tmp = worldDir.resolve(FILE + ".tmp");
        try (OutputStream out = Files.newOutputStream(tmp)) {
            props.store(out, "MCPersist: keep the key secret; it holds this world's address");
        }
        Files.move(tmp, worldDir.resolve(FILE), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }
}
