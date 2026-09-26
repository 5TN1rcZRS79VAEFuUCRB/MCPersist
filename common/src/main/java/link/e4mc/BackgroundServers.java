package link.e4mc;

import link.e4mc.handoff.Handoff;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.resolver.ServerAddress;

import java.io.IOException;
import java.nio.file.Path;

/** The client's view of its worlds' background servers. */
public final class BackgroundServers {
    private BackgroundServers() {}

    public static Path serversDir() {
        return Agnos.gameDir().resolve("mcpersist").resolve("servers");
    }

    private static Path worldDir(String levelId) {
        return Minecraft.getInstance().getLevelSource().getLevelPath(levelId);
    }

    public static boolean isRunning(String levelId) {
        return Handoff.isRunning(serversDir(), worldDir(levelId));
    }

    /** Joins the world's background server instead of opening it in singleplayer. */
    public static void join(Screen parent, String levelId, String worldName) throws IOException {
        int port = Handoff.localPort(serversDir(), worldDir(levelId));
        String address = "127.0.0.1:" + port;
        Minecraft minecraft = Minecraft.getInstance();
        ConnectScreen.startConnecting(parent, minecraft, new ServerAddress("127.0.0.1", port),
                new ServerData(worldName, address, ServerData.Type.OTHER), false, null);
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

    public static void stop(String levelId, Runnable then) {
        stop(worldDir(levelId), then);
    }
}
