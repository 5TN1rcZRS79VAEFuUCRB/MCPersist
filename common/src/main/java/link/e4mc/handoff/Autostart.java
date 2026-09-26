package link.e4mc.handoff;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;

/**
 * One OS-native entry per background server that starts it when the user logs in, without
 * the game: a Startup-folder script on Windows, a LaunchAgent on macOS, a systemd user unit
 * elsewhere. Each runs {@link Launcher}.
 */
final class Autostart {
    private static final String LAUNCHER = Launcher.class.getName();

    private Autostart() {}

    private enum Os { WINDOWS, MAC, LINUX }

    private static Os os() {
        String name = System.getProperty("os.name").toLowerCase(Locale.ROOT);
        return name.startsWith("windows") ? Os.WINDOWS : name.startsWith("mac") ? Os.MAC : Os.LINUX;
    }

    private static Path home() {
        return Path.of(System.getProperty("user.home"));
    }

    /** A name for the server's entry, safe in unit, plist and file names. */
    private static String id(Path serverDir) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256")
                    .digest(serverDir.toAbsolutePath().toString().getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(hash, 0, 6);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    /** The files that make up the server's entry; the first is the entry itself. */
    static List<Path> files(Path serverDir) {
        String id = id(serverDir);
        return switch (os()) {
            // ponytail: assumes the default AppData location; roaming-profile redirects aren't followed.
            case WINDOWS -> List.of(home().resolve("AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup")
                    .resolve("mcpersist-" + id + ".cmd"));
            case MAC -> List.of(home().resolve("Library/LaunchAgents").resolve("link.mcpersist." + id + ".plist"));
            case LINUX -> {
                Path units = configHome().resolve("systemd/user");
                String unit = "mcpersist-" + id + ".service";
                yield List.of(units.resolve(unit), units.resolve("default.target.wants").resolve(unit));
            }
        };
    }

    /** Where systemd looks for user units: $XDG_CONFIG_HOME, else ~/.config. */
    private static Path configHome() {
        String xdg = System.getenv("XDG_CONFIG_HOME");
        return xdg != null && !xdg.isEmpty() ? Path.of(xdg) : home().resolve(".config");
    }

    static void install(Path serverDir, Path java) throws IOException {
        Path dir = serverDir.toAbsolutePath();
        Path jar = dir.resolve(Launcher.LAUNCHER_JAR);
        List<Path> files = files(dir);
        Files.createDirectories(files.get(0).getParent());
        switch (os()) {
            case WINDOWS -> {
                // javaw, so no console window stays open at login.
                Path javaw = java.resolveSibling("javaw.exe");
                Path exe = Files.exists(javaw) ? javaw : java;
                Files.writeString(files.get(0), "@start \"\" /min " + cmdQuote(exe) + " -cp " + cmdQuote(jar) + " "
                        + LAUNCHER + " " + cmdQuote(dir) + "\r\n");
            }
            case MAC -> Files.writeString(files.get(0), """
                    <?xml version="1.0" encoding="UTF-8"?>
                    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
                    <plist version="1.0">
                    <dict>
                        <key>Label</key>
                        <string>link.mcpersist.%s</string>
                        <key>ProgramArguments</key>
                        <array>
                            <string>%s</string>
                            <string>-cp</string>
                            <string>%s</string>
                            <string>%s</string>
                            <string>%s</string>
                        </array>
                        <key>RunAtLoad</key>
                        <true/>
                    </dict>
                    </plist>
                    """.formatted(id(dir), xml(java), xml(jar), LAUNCHER, xml(dir)));
            case LINUX -> {
                Files.writeString(files.get(0), """
                        [Unit]
                        Description=MCPersist background server for %s

                        [Service]
                        Type=simple
                        ExecStart=%s -cp %s %s %s

                        [Install]
                        WantedBy=default.target
                        """.formatted(dir.getFileName(), unitQuote(java), unitQuote(jar), LAUNCHER, unitQuote(dir)));
                // What `systemctl --user enable` does, without needing a running user manager.
                Files.createDirectories(files.get(1).getParent());
                Files.deleteIfExists(files.get(1));
                Files.createSymbolicLink(files.get(1), Path.of("..", files.get(0).getFileName().toString()));
            }
        }
    }

    static void remove(Path serverDir) throws IOException {
        for (Path file : files(serverDir.toAbsolutePath())) {
            Files.deleteIfExists(file);
        }
    }

    private static String cmdQuote(Path path) {
        return "\"" + path.toString().replace("%", "%%") + "\"";
    }

    private static String unitQuote(Path path) {
        return "\"" + path.toString().replace("\\", "\\\\").replace("\"", "\\\"").replace("%", "%%") + "\"";
    }

    private static String xml(Path path) {
        return path.toString().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
    }
}
