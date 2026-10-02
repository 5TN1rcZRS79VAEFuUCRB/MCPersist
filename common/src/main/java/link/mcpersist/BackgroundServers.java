package link.mcpersist;

import io.netty.buffer.ByteBuf;
import io.netty.buffer.ByteBufUtil;
import io.netty.buffer.Unpooled;
import link.mcpersist.handoff.Handoff;
import net.minecraft.client.Minecraft;
import net.minecraft.client.User;
import net.minecraft.client.gui.screens.AlertScreen;
import net.minecraft.client.gui.screens.ConfirmScreen;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.GenericMessageScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.VarInt;
import net.minecraft.network.chat.Component;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.resolver.ServerAddress;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/** The client's view of its worlds' background servers. */
public final class BackgroundServers {
    private BackgroundServers() {}

    private static Path worldDir(String levelId) {
        return Minecraft.getInstance().getLevelSource().getLevelPath(levelId);
    }

    /** Worlds whose background server is being stopped: not joinable, though still up for a moment. */
    private static final Set<Path> STOPPING = ConcurrentHashMap.newKeySet();

    public static boolean isRunning(String levelId) {
        Path worldDir = worldDir(levelId);
        return !STOPPING.contains(worldDir) && Handoff.isRunning(LocalHandoff.serversDir(), worldDir);
    }

    /** The server entry of the background server last joined from the world list. */
    public static volatile ServerData joined;

    /**
     * Joins the world's background server instead of opening it in singleplayer, first waiting
     * for it to accept players: right after a handoff it's running but still starting.
     */
    public static void join(Screen parent, String levelId, String worldName) throws IOException {
        int port = Handoff.localPort(LocalHandoff.serversDir(), worldDir(levelId));
        Minecraft minecraft = Minecraft.getInstance();
        ServerData server = new ServerData(worldName, "127.0.0.1:" + port, ServerData.Type.OTHER);
        minecraft.gui.setScreen(new GenericMessageScreen(Component.translatable("selectWorld.mcpersist.waitingForBackground")));
        new Thread(() -> {
            boolean up = waitForStatus(port, 60_000);
            minecraft.execute(() -> {
                if (up) {
                    joined = server;
                    ConnectScreen.startConnecting(parent, minecraft, new ServerAddress("127.0.0.1", port), server, false, null);
                } else {
                    minecraft.gui.setScreen(new AlertScreen(() -> minecraft.gui.setScreen(parent),
                            Component.translatable("selectWorld.mcpersist.backgroundNotStarting"),
                            Component.translatable("selectWorld.mcpersist.backgroundNotStarting.message")));
                }
            });
        }, "MCPersist background join").start();
    }

    /**
     * The port opens before the server is up, and logins are dropped until its first tick;
     * a server-list ping is only answered from then on.
     */
    private static boolean waitForStatus(int port, long timeoutMillis) {
        long deadline = System.currentTimeMillis() + timeoutMillis;
        while (System.currentTimeMillis() < deadline) {
            try (Socket socket = new Socket()) {
                // The address the server binds (server-ip) and the join uses. Not the loopback
                // address: Forge's IPv6 preference can make that ::1, where nothing listens.
                socket.connect(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), port), 1000);
                socket.setSoTimeout(2000);
                byte[] host = "127.0.0.1".getBytes(StandardCharsets.UTF_8);
                ByteBuf handshake = Unpooled.buffer();
                VarInt.write(handshake, 0);
                VarInt.write(handshake, -1);
                VarInt.write(handshake, host.length);
                handshake.writeBytes(host).writeShort(port);
                VarInt.write(handshake, 1);
                ByteBuf out = VarInt.write(Unpooled.buffer(), handshake.readableBytes()).writeBytes(handshake);
                out.writeBytes(new byte[]{1, 0});
                socket.getOutputStream().write(ByteBufUtil.getBytes(out));
                handshake.release();
                out.release();
                byte[] reply = socket.getInputStream().readNBytes(64);
                if (new String(reply, StandardCharsets.UTF_8).contains("{")) {
                    return true;
                }
                throw new IOException("not answering pings yet");
            } catch (IOException notYet) {
                try {
                    Thread.sleep(500);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return false;
                }
            }
        }
        return false;
    }

    /**
     * Stops the world's background server off the render thread, then runs {@code then} on it.
     * Stopped means offline until the world is shared again, reboots included.
     */
    public static void stop(Path worldDir, Runnable then) {
        STOPPING.add(worldDir);
        new Thread(() -> {
            try {
                Handoff.disable(LocalHandoff.serversDir(), worldDir);
            } catch (IOException e) {
                MCPersist.LOGGER.error("Failed to stop the background server for {}", worldDir, e);
            } finally {
                STOPPING.remove(worldDir);
            }
            Minecraft.getInstance().execute(then);
        }, "mcpersist-stop").start();
    }

    public static void stop(String levelId, Runnable then) {
        stop(worldDir(levelId), then);
    }

    private static boolean problemsShown;

    /**
     * Once per game: tells the host about background servers that failed, and offers to
     * start one for a world whose session ended without a handoff. Returns to {@code screen}.
     */
    public static void showProblemsOnce(Screen screen) {
        if (problemsShown) {
            return;
        }
        problemsShown = true;
        try {
            showProblems(screen, Handoff.takeProblems(LocalHandoff.serversDir()), 0);
        } catch (IOException e) {
            MCPersist.LOGGER.error("Failed to check background servers", e);
        }
    }

    private static void showProblems(Screen screen, List<Handoff.Problem> problems, int index) {
        Minecraft minecraft = Minecraft.getInstance();
        if (index == problems.size()) {
            minecraft.gui.setScreen(screen);
            return;
        }
        Handoff.Problem problem = problems.get(index);
        Runnable next = () -> showProblems(screen, problems, index + 1);
        Component world = Component.literal(problem.worldDir().getFileName().toString());
        if (problem.kind() == Handoff.Problem.Kind.FAILED) {
            minecraft.gui.setScreen(new AlertScreen(next,
                    Component.translatable("mcpersist.problem.failed.title", world),
                    Component.translatable("mcpersist.problem.failed.message", problem.log().toString())));
        } else {
            minecraft.gui.setScreen(new ConfirmScreen(start -> {
                if (start) {
                    User user = minecraft.getUser();
                    Handoff.Player host = new Handoff.Player(user.getProfileId(), user.getName());
                    // Friends come from the host's whitelist, or the server's own from earlier sessions.
                    new Thread(() -> LocalHandoff.start(problem.worldDir(), host, List.of()), "mcpersist-handoff").start();
                }
                next.run();
            }, Component.translatable("mcpersist.problem.interrupted.title", world),
                    Component.translatable("mcpersist.problem.interrupted.message")));
        }
    }
}
