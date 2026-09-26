package link.e4mc.handoff;

import java.io.IOException;
import java.io.InputStream;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.channels.OverlappingFileLockException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;

/**
 * Starts a background server from its folder. Uses only the JDK: autostart entries run it
 * with nothing but the copied launcher jar on the classpath ({@link #main}).
 */
public final class Launcher {
    static final String SERVER_JAR = "fabric-server-launch.jar";
    static final String LAUNCH_FILE = "mcpersist-launch.txt";
    static final String PID_FILE = "mcpersist.pid";
    static final String FAILURE_FILE = "mcpersist-failure.txt";
    static final String CONSOLE_LOG = "mcpersist-console.log";
    /** A copy of the jar this class is in, for autostart entries. */
    static final String LAUNCHER_JAR = "mcpersist-launcher.jar";

    private Launcher() {}

    /** A running game or server holds session.lock; never start a second one on the world. */
    static void requireUnlocked(Path worldDir) throws IOException {
        Path lock = worldDir.resolve("session.lock");
        if (!Files.exists(lock)) {
            return;
        }
        try (FileChannel channel = FileChannel.open(lock, StandardOpenOption.WRITE)) {
            FileLock held = channel.tryLock();
            if (held == null) {
                throw new IllegalStateException(worldDir + " is open in another game or server");
            }
            held.release();
        } catch (OverlappingFileLockException e) {
            throw new IllegalStateException(worldDir + " is still open in this game", e);
        }
    }

    /** Starts the server with its saved launch command, logging to its console log. */
    static Process launch(Path dir) throws IOException {
        List<String> command = new ArrayList<>();
        // On Linux, a new session keeps the server out of the game's process group, so it
        // survives the terminal or launcher that started the game going away.
        if (Files.isExecutable(Path.of("/usr/bin/setsid"))) {
            command.add("/usr/bin/setsid");
        }
        command.addAll(Files.readAllLines(dir.resolve(LAUNCH_FILE)));
        Files.createDirectories(dir.resolve("logs"));
        Process process = new ProcessBuilder(command)
                .directory(dir.toFile())
                .redirectErrorStream(true)
                .redirectOutput(ProcessBuilder.Redirect.to(dir.resolve("logs").resolve(CONSOLE_LOG).toFile()))
                .start();
        process.getOutputStream().close();
        Files.writeString(dir.resolve(PID_FILE), Long.toString(process.pid()));
        return process;
    }

    /** Records a start that failed before the server could run, for the next game launch. */
    static void recordFailure(Path dir, Path worldDir, Exception e) throws IOException {
        Files.createDirectories(dir);
        Files.writeString(dir.resolve(FAILURE_FILE), worldDir.toAbsolutePath() + "\n" + e);
    }

    static Path worldDir(Path dir) throws IOException {
        Properties props = new Properties();
        try (InputStream in = Files.newInputStream(dir.resolve("server.properties"))) {
            props.load(in);
        }
        return Path.of(props.getProperty("level-name"));
    }

    /**
     * What an autostart entry runs: starts the server in {@code args[0]} unless its world is
     * open, and waits for it, since systemd and launchd stop what a finished job leaves behind.
     */
    public static void main(String[] args) throws Exception {
        Path dir = Path.of(args[0]);
        Path worldDir = worldDir(dir);
        try {
            requireUnlocked(worldDir);
        } catch (IllegalStateException alreadyOpen) {
            return;
        }
        try {
            System.exit(launch(dir).waitFor());
        } catch (IOException e) {
            recordFailure(dir, worldDir, e);
            throw e;
        }
    }
}
