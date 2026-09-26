package link.e4mc;

import link.e4mc.handoff.Handoff;
import net.minecraft.client.Minecraft;
import net.minecraft.client.User;
import net.minecraft.client.gui.screens.AlertScreen;
import net.minecraft.client.gui.screens.ConfirmScreen;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.resolver.ServerAddress;

import java.io.IOException;
import java.nio.file.Path;
import java.util.List;

/** The client's view of its worlds' background servers. */
public final class BackgroundServers {
    private BackgroundServers() {}

    public static Path serversDir() {
        return LocalHandoff.serversDir();
    }

    private static Path worldDir(String levelId) {
        return Minecraft.getInstance().getLevelSource().getLevelPath(levelId);
    }

    public static boolean isRunning(String levelId) {
        return Handoff.isRunning(serversDir(), worldDir(levelId));
    }

    /** The server entry of the background server last joined from the world list. */
    public static volatile ServerData joined;

    /** Joins the world's background server instead of opening it in singleplayer. */
    public static void join(Screen parent, String levelId, String worldName) throws IOException {
        int port = Handoff.localPort(serversDir(), worldDir(levelId));
        String address = "127.0.0.1:" + port;
        Minecraft minecraft = Minecraft.getInstance();
        joined = new ServerData(worldName, address, ServerData.Type.OTHER);
        ConnectScreen.startConnecting(parent, minecraft, new ServerAddress("127.0.0.1", port), joined, false, null);
    }

    /** Stops the world's background server off the render thread, then runs {@code then} on it. */
    public static void stop(Path worldDir, Runnable then) {
        new Thread(() -> {
            try {
                Handoff.stop(serversDir(), worldDir);
            } catch (IOException e) {
                E4mcClient.LOGGER.error("Failed to stop the background server for {}", worldDir, e);
            }
            Minecraft.getInstance().execute(then);
        }, "mcpersist-stop").start();
    }

    /** Turning persistence off: stops the server and removes its autostart entry. */
    public static void disable(Path worldDir) {
        new Thread(() -> {
            try {
                Handoff.disable(serversDir(), worldDir);
            } catch (IOException e) {
                E4mcClient.LOGGER.error("Failed to disable the background server for {}", worldDir, e);
            }
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
            showProblems(screen, Handoff.takeProblems(serversDir()), 0);
        } catch (IOException e) {
            E4mcClient.LOGGER.error("Failed to check background servers", e);
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
                    // Friends from earlier sessions are already on the whitelist.
                    new Thread(() -> LocalHandoff.start(problem.worldDir(), host, List.of()), "mcpersist-handoff").start();
                }
                next.run();
            }, Component.translatable("mcpersist.problem.interrupted.title", world),
                    Component.translatable("mcpersist.problem.interrupted.message")));
        }
    }
}
