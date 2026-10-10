package link.mcpersist;

import java.io.IOException;
import java.io.Reader;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.util.Properties;

/**
 * MCPersist's settings, read once from config/mcpersist/mcpersist.toml. A missing file is
 * written with the defaults.
 */
// ponytail: flat key = value TOML only, read with Properties (quotes stripped); a real TOML
// parser if the file ever needs tables, arrays or escapes.
public final class Config {
    private static final Path FILE = Agnos.configDir().resolve("mcpersist").resolve("mcpersist.toml");
    private static final Properties VALUES = new Properties();
    private static final StringBuilder DEFAULTS = new StringBuilder();

    static {
        try (Reader in = Files.newBufferedReader(FILE)) {
            VALUES.load(in);
        } catch (NoSuchFileException missing) {
        } catch (IOException e) {
            MCPersist.LOGGER.error("Failed to read {}; using the defaults", FILE, e);
        }
    }

    public static final boolean hideDomainInChat = flag("hideDomainInChat", false,
            "Whether to hide the domain on chat and only allow copying");
    // Not relayHost, which older versions wrote to every config file as relay.mcpersist.com.
    public static final String relay = text("relay", "",
            "The relay to host through, e.g. relay.eu.mcpersist.com; empty for the nearest one");
    // The smoke test points this at its local relay.
    public static final int relayPort = Integer.parseInt(read("relayPort", "25575",
            "The relay's port", "25575"));
    public static final boolean restoreDedicatedCommands = flag("restoreDedicatedCommands", true,
            "Allows use of certain dedicated server commands such as /ban and /whitelist");
    // On: a world's address stays the same across sessions, so anyone who learns it could come back.
    public static final boolean useWhiteList = flag("useWhiteList", true,
            "Whether to use whitelists on LAN worlds");
    public static final boolean requireMod = flag("requireMod", false,
            "Whether only players with MCPersist can join; for public servers, so the mod gets around");
    public static final boolean hostEnabled = flag("hostEnabled", true,
            "Whether to enable sharing LAN worlds with MCPersist");
    public static final boolean dialtoneHostEnabled = flag("dialtoneHostEnabled", true,
            "Whether to enable Dialtone peer-to-peer connections as the host");
    public static final boolean dialtonePlayerEnabled = flag("dialtonePlayerEnabled", true,
            "Whether to enable Dialtone peer-to-peer connections as the player");
    public static final boolean checkForUpdates = flag("checkForUpdates", true,
            "Whether to say in chat when a newer MCPersist is out, once you start sharing a world");

    static {
        if (!Files.exists(FILE)) {
            try {
                Files.createDirectories(FILE.getParent());
                Files.writeString(FILE, DEFAULTS);
            } catch (IOException e) {
                MCPersist.LOGGER.error("Failed to write {}", FILE, e);
            }
        }
    }

    private Config() {}

    /** Loads the settings, writing the file if it's missing. */
    public static void load() {}

    private static boolean flag(String key, boolean fallback, String comment) {
        return Boolean.parseBoolean(read(key, Boolean.toString(fallback), comment, Boolean.toString(fallback)));
    }

    private static String text(String key, String fallback, String comment) {
        return read(key, fallback, comment, '"' + fallback + '"');
    }

    private static String read(String key, String fallback, String comment, String asToml) {
        DEFAULTS.append("# ").append(comment).append('\n').append(key).append(" = ").append(asToml).append("\n\n");
        String value = VALUES.getProperty(key);
        return value == null ? fallback : value.replaceAll("^\"|\"$", "");
    }
}
