package link.e4mc.handoff;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import link.e4mc.WorldPersistence;

import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.DataInputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.OverlappingFileLockException;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.SecureRandom;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Properties;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/**
 * Hands a persistent world to a headless Fabric server running in the background on this
 * machine. The server runs the world in place from its saves folder; its own files live in a
 * per-world folder outside saves. Uses only the JDK and Gson, so it also runs from the
 * command line ({@link #main}) for tests.
 */
public final class Handoff {
    private static final String FABRIC_META = "https://meta.fabricmc.net/v2/versions";
    private static final String SERVER_JAR = "fabric-server-launch.jar";
    static final String LAUNCH_FILE = "mcpersist-launch.txt";
    static final String PID_FILE = "mcpersist.pid";
    private static final Gson GSON = new Gson();
    private static final UUID NIL = new UUID(0, 0);

    public record Player(UUID id, String name) {}

    /**
     * @param worldDir        the world, in the client's saves folder
     * @param clientModsDir   the client's mods folder; its non-client-only mods are copied
     * @param clientConfigDir the client's config folder; MCPersist's config is copied
     * @param serversDir      where per-world server folders live
     * @param java            the Java executable to run the server with
     * @param maxHeap         the -Xmx value, e.g. "4G"
     * @param host            the world's owner: whitelisted, op level 4
     * @param players         everyone who joined while the host played: whitelisted
     */
    public record Spec(Path worldDir, Path clientModsDir, Path clientConfigDir, Path serversDir,
                       String minecraftVersion, String loaderVersion, Path java, String maxHeap,
                       Player host, List<Player> players) {}

    private Handoff() {}

    public static Path serverDir(Path serversDir, Path worldDir) {
        return serversDir.resolve(worldDir.getFileName().toString());
    }

    /** Prepares the world's server folder and starts the server, detached. Returns the folder. */
    public static Path start(Spec spec) throws IOException, InterruptedException {
        if (!WorldPersistence.isPersistent(spec.worldDir())) {
            throw new IllegalStateException(spec.worldDir() + " is not a persistent world");
        }
        requireUnlocked(spec.worldDir());
        Path dir = serverDir(spec.serversDir(), spec.worldDir());
        Files.createDirectories(dir);
        installServer(dir, spec.minecraftVersion(), spec.loaderVersion());
        copyMods(spec.clientModsDir(), dir.resolve("mods"));
        copyConfig(spec.clientConfigDir(), dir.resolve("config"));
        // The host turned persistence on for this world, which runs it as a Minecraft server.
        Files.writeString(dir.resolve("eula.txt"), "eula=true\n");
        writeServerProperties(dir, spec.worldDir());
        writeAccessLists(dir, spec.host(), spec.players());
        recoverHostPlayerData(spec.worldDir(), spec.host());

        List<String> command = List.of(spec.java().toString(), "-Xmx" + spec.maxHeap(), "-jar", SERVER_JAR, "nogui");
        Files.write(dir.resolve(LAUNCH_FILE), command);
        long pid = launchDetached(dir, command);
        Files.writeString(dir.resolve(PID_FILE), Long.toString(pid));
        return dir;
    }

    /** A running game or server holds session.lock; never start a second one on the world. */
    private static void requireUnlocked(Path worldDir) throws IOException {
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

    private static void installServer(Path dir, String minecraftVersion, String loaderVersion) throws IOException, InterruptedException {
        Path versionFile = dir.resolve("mcpersist-server-version.txt");
        String version = minecraftVersion + " " + loaderVersion;
        if (Files.exists(dir.resolve(SERVER_JAR)) && Files.exists(versionFile)
                && Files.readString(versionFile).equals(version)) {
            return;
        }
        JsonArray installers = GSON.fromJson(get(FABRIC_META + "/installer"), JsonArray.class);
        String installer = installers.get(0).getAsJsonObject().get("version").getAsString();
        download(FABRIC_META + "/loader/" + minecraftVersion + "/" + loaderVersion + "/" + installer + "/server/jar",
                dir.resolve(SERVER_JAR));
        Files.writeString(versionFile, version);
    }

    /** Replaces the server's mods with the client's, minus mods that only run on clients. */
    static void copyMods(Path from, Path to) throws IOException {
        Files.createDirectories(to);
        try (DirectoryStream<Path> old = Files.newDirectoryStream(to, "*.jar")) {
            for (Path jar : old) {
                Files.delete(jar);
            }
        }
        if (!Files.isDirectory(from)) {
            return;
        }
        try (DirectoryStream<Path> jars = Files.newDirectoryStream(from, "*.jar")) {
            for (Path jar : jars) {
                if (!isClientOnly(jar)) {
                    Files.copy(jar, to.resolve(jar.getFileName()));
                }
            }
        }
    }

    static boolean isClientOnly(Path jar) {
        try (ZipFile zip = new ZipFile(jar.toFile())) {
            ZipEntry entry = zip.getEntry("fabric.mod.json");
            if (entry == null) {
                return false;
            }
            try (InputStream in = zip.getInputStream(entry)) {
                JsonObject metadata = GSON.fromJson(new InputStreamReader(in, StandardCharsets.UTF_8), JsonObject.class);
                return metadata.has("environment") && "client".equals(metadata.get("environment").getAsString());
            }
        } catch (IOException | RuntimeException e) {
            // Unreadable metadata: let the server's loader decide.
            return false;
        }
    }

    private static void copyConfig(Path clientConfigDir, Path serverConfigDir) throws IOException {
        Path config = clientConfigDir.resolve("mcpersist").resolve("mcpersist.toml");
        if (Files.exists(config)) {
            Files.createDirectories(serverConfigDir.resolve("mcpersist"));
            Files.copy(config, serverConfigDir.resolve("mcpersist").resolve("mcpersist.toml"), StandardCopyOption.REPLACE_EXISTING);
        }
    }

    /** Keeps any other settings the host changed; sets the ones the handoff depends on. */
    private static void writeServerProperties(Path dir, Path worldDir) throws IOException {
        Path file = dir.resolve("server.properties");
        Properties props = new Properties();
        if (Files.exists(file)) {
            try (InputStream in = Files.newInputStream(file)) {
                props.load(in);
            }
        }
        // Forward slashes on every OS: the server reads this with java.util.Properties.
        props.setProperty("level-name", worldDir.toAbsolutePath().toString().replace('\\', '/'));
        props.setProperty("accepts-transfers", "true");
        // Players arrive through the relay; the local port is only for the host's own client.
        props.setProperty("server-ip", "127.0.0.1");
        props.setProperty("server-port", Integer.toString(freePort()));
        // How MCPersist stops the server cleanly: it runs detached, with no console to type into.
        props.setProperty("enable-rcon", "true");
        props.setProperty("rcon.port", Integer.toString(freePort()));
        props.setProperty("rcon.password", randomPassword());
        // A stable address gets found; only the host and their friends may join.
        props.setProperty("white-list", "true");
        props.setProperty("enforce-whitelist", "true");
        props.setProperty("online-mode", "true");
        try (OutputStream out = Files.newOutputStream(file)) {
            props.store(out, "Written by MCPersist; level-name, accepts-transfers, server-ip and server-port are managed");
        }
    }

    /** Adds the session's players to the whitelist and the host as op, keeping existing entries. */
    private static void writeAccessLists(Path dir, Player host, List<Player> players) throws IOException {
        Map<UUID, JsonObject> whitelist = readList(dir.resolve("whitelist.json"));
        List<Player> allowed = new ArrayList<>(players);
        allowed.add(host);
        for (Player player : allowed) {
            whitelist.putIfAbsent(player.id(), entry(player));
        }
        writeList(dir.resolve("whitelist.json"), whitelist);

        Map<UUID, JsonObject> ops = readList(dir.resolve("ops.json"));
        JsonObject op = entry(host);
        op.addProperty("level", 4);
        op.addProperty("bypassesPlayerLimit", false);
        ops.put(host.id(), op);
        writeList(dir.resolve("ops.json"), ops);
    }

    private static JsonObject entry(Player player) {
        JsonObject entry = new JsonObject();
        entry.addProperty("uuid", player.id().toString());
        entry.addProperty("name", player.name());
        return entry;
    }

    private static Map<UUID, JsonObject> readList(Path file) throws IOException {
        Map<UUID, JsonObject> entries = new LinkedHashMap<>();
        if (Files.exists(file)) {
            for (var element : GSON.fromJson(Files.readString(file), JsonArray.class)) {
                JsonObject entry = element.getAsJsonObject();
                entries.put(UUID.fromString(entry.get("uuid").getAsString()), entry);
            }
        }
        return entries;
    }

    private static void writeList(Path file, Map<UUID, JsonObject> entries) throws IOException {
        JsonArray array = new JsonArray();
        entries.values().forEach(array::add);
        Files.writeString(file, GSON.toJson(array));
    }

    /**
     * A world migrated from an old save can hold the host's player under the all-zeros UUID,
     * which a dedicated server never loads: the host would arrive with an empty inventory.
     */
    static void recoverHostPlayerData(Path worldDir, Player host) throws IOException {
        Path data = worldDir.resolve("players").resolve("data");
        Path hostFile = data.resolve(host.id() + ".dat");
        Path nilFile = data.resolve(NIL + ".dat");
        if (!Files.exists(hostFile) && Files.exists(nilFile)) {
            Files.copy(nilFile, hostFile);
        }
    }

    private static String randomPassword() {
        byte[] bytes = new byte[18];
        new SecureRandom().nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    private static Properties serverProperties(Path dir) throws IOException {
        Properties props = new Properties();
        try (InputStream in = Files.newInputStream(dir.resolve("server.properties"))) {
            props.load(in);
        }
        return props;
    }

    /** The background server's process, if it is running. */
    private static Optional<ProcessHandle> process(Path dir) {
        Path pidFile = dir.resolve(PID_FILE);
        if (!Files.exists(pidFile)) {
            return Optional.empty();
        }
        try {
            return ProcessHandle.of(Long.parseLong(Files.readString(pidFile).trim()))
                    .filter(ProcessHandle::isAlive)
                    // A reused PID belongs to some other program.
                    .filter(p -> p.info().commandLine().map(c -> c.contains(SERVER_JAR)).orElse(true));
        } catch (IOException | NumberFormatException e) {
            return Optional.empty();
        }
    }

    public static boolean isRunning(Path serversDir, Path worldDir) {
        return process(serverDir(serversDir, worldDir)).isPresent();
    }

    /** The loopback port the host's own client connects to. */
    public static int localPort(Path serversDir, Path worldDir) throws IOException {
        return Integer.parseInt(serverProperties(serverDir(serversDir, worldDir)).getProperty("server-port"));
    }

    public enum StopResult { NOT_RUNNING, STOPPED, TERMINATED }

    /**
     * Stops the world's background server with its own stop command, which saves the world.
     * If that fails, or it doesn't stop within a minute, terminates the process instead
     * (which still saves on Linux and macOS, but not on Windows).
     */
    public static StopResult stop(Path serversDir, Path worldDir) throws IOException {
        Path dir = serverDir(serversDir, worldDir);
        Optional<ProcessHandle> process = process(dir);
        if (process.isEmpty()) {
            return StopResult.NOT_RUNNING;
        }
        StopResult result = StopResult.STOPPED;
        try {
            Properties props = serverProperties(dir);
            rcon(Integer.parseInt(props.getProperty("rcon.port")), props.getProperty("rcon.password"), "stop");
        } catch (IOException | RuntimeException e) {
            result = StopResult.TERMINATED;
            process.get().destroy();
        }
        try {
            process.get().onExit().get(60, TimeUnit.SECONDS);
        } catch (Exception e) {
            result = StopResult.TERMINATED;
            process.get().destroyForcibly();
        }
        Files.deleteIfExists(dir.resolve(PID_FILE));
        return result;
    }

    /** Minimal RCON client: log in, run one command. */
    static void rcon(int port, String password, String command) throws IOException {
        try (Socket socket = new Socket(InetAddress.getLoopbackAddress(), port)) {
            socket.setSoTimeout((int) Duration.ofSeconds(10).toMillis());
            OutputStream out = socket.getOutputStream();
            DataInputStream in = new DataInputStream(socket.getInputStream());
            out.write(rconPacket(1, 3, password));
            if (readRconId(in) != 1) {
                throw new IOException("RCON login refused");
            }
            out.write(rconPacket(2, 2, command));
            readRconId(in);
        }
    }

    private static byte[] rconPacket(int id, int type, String body) {
        byte[] payload = body.getBytes(StandardCharsets.UTF_8);
        return ByteBuffer.allocate(4 + 4 + 4 + payload.length + 2).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(4 + 4 + payload.length + 2).putInt(id).putInt(type).put(payload).put((byte) 0).put((byte) 0)
                .array();
    }

    private static int readRconId(DataInputStream in) throws IOException {
        byte[] header = new byte[8];
        in.readFully(header);
        ByteBuffer buffer = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN);
        int length = buffer.getInt();
        int id = buffer.getInt();
        in.readFully(new byte[length - 4]);
        return id;
    }

    private static int freePort() throws IOException {
        try (ServerSocket socket = new ServerSocket(0)) {
            return socket.getLocalPort();
        }
    }

    /** Starts the server so it keeps running after this JVM exits. */
    private static long launchDetached(Path dir, List<String> command) throws IOException {
        List<String> full = new ArrayList<>();
        // On Linux, a new session keeps the server out of the game's process group, so it
        // survives the terminal or launcher that started the game going away.
        if (Files.isExecutable(Path.of("/usr/bin/setsid"))) {
            full.add("/usr/bin/setsid");
        }
        full.addAll(command);
        Files.createDirectories(dir.resolve("logs"));
        Process process = new ProcessBuilder(full)
                .directory(dir.toFile())
                .redirectErrorStream(true)
                .redirectOutput(ProcessBuilder.Redirect.appendTo(dir.resolve("logs").resolve("mcpersist-console.log").toFile()))
                .start();
        process.getOutputStream().close();
        return process.pid();
    }

    private static String get(String url) throws IOException, InterruptedException {
        HttpResponse<String> response = HttpClient.newHttpClient().send(
                HttpRequest.newBuilder(URI.create(url)).header("User-Agent", "MCPersist").build(),
                HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw new IOException("GET " + url + " returned " + response.statusCode());
        }
        return response.body();
    }

    private static void download(String url, Path dest) throws IOException, InterruptedException {
        Path tmp = dest.resolveSibling(dest.getFileName() + ".part");
        HttpResponse<Path> response = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NORMAL).build().send(
                HttpRequest.newBuilder(URI.create(url)).header("User-Agent", "MCPersist").build(),
                HttpResponse.BodyHandlers.ofFile(tmp));
        if (response.statusCode() != 200) {
            Files.deleteIfExists(tmp);
            throw new IOException("GET " + url + " returned " + response.statusCode());
        }
        Files.move(tmp, dest, StandardCopyOption.REPLACE_EXISTING);
    }

    /**
     * Command-line entry point, for tests. {@code --action start} (the default) takes {@code
     * --world --mods --config --servers --minecraft --loader --host uuid:name [--players
     * uuid:name,...] [--java] [--xmx]} and prints the server folder once it has started;
     * {@code --action status|stop} take {@code --world --servers}.
     */
    public static void main(String[] args) throws Exception {
        Properties options = new Properties();
        for (int i = 0; i + 1 < args.length; i += 2) {
            options.setProperty(args[i].replaceFirst("^--", ""), args[i + 1]);
        }
        Path world = Path.of(options.getProperty("world"));
        Path servers = Path.of(options.getProperty("servers"));
        switch (options.getProperty("action", "start")) {
            case "status" -> {
                System.out.println(isRunning(servers, world) ? "running" : "stopped");
                return;
            }
            case "stop" -> {
                System.out.println(stop(servers, world).name().toLowerCase());
                return;
            }
            case "start" -> {}
            default -> throw new IllegalArgumentException("unknown --action");
        }
        Path java = options.containsKey("java")
                ? Path.of(options.getProperty("java"))
                : ProcessHandle.current().info().command().map(Path::of).orElseThrow();
        Path dir = start(new Spec(
                world,
                Path.of(options.getProperty("mods")),
                Path.of(options.getProperty("config")),
                servers,
                options.getProperty("minecraft"),
                options.getProperty("loader"),
                java,
                options.getProperty("xmx", "1G"),
                player(options.getProperty("host")),
                options.getProperty("players", "").isEmpty() ? List.of()
                        : Arrays.stream(options.getProperty("players").split(",")).map(Handoff::player).toList()));
        System.out.println(dir);
    }

    private static Player player(String idAndName) {
        String[] parts = idAndName.split(":", 2);
        return new Player(UUID.fromString(parts[0]), parts[1]);
    }
}
