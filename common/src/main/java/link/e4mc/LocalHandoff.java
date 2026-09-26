package link.e4mc;

import link.e4mc.handoff.Handoff;
import net.minecraft.server.players.PlayerList;

import java.lang.management.ManagementFactory;
import java.nio.file.Path;
import java.util.List;

/** Hands this game's worlds to background servers using this game's own setup. */
public final class LocalHandoff {
    // ponytail: Fabric only until Handoff can install NeoForge and Forge servers (#22, #24).
    public static final boolean SUPPORTED = Agnos.LOADER.equals("fabric");

    private LocalHandoff() {}

    public static Path serversDir() {
        return Agnos.gameDir().resolve("mcpersist").resolve("servers");
    }

    /** Starts the world's background server; a failure is recorded for the next launch. */
    public static void start(Path worldDir, Handoff.Player host, List<Handoff.Player> players) {
        Path gameDir = Agnos.gameDir();
        Handoff.Spec spec = new Handoff.Spec(
                worldDir,
                gameDir.resolve("mods"),
                Agnos.configDir(),
                serversDir(),
                Agnos.modVersion("minecraft"),
                Agnos.modVersion("fabricloader"),
                ProcessHandle.current().info().command().map(Path::of).orElseThrow(),
                maxHeap(),
                host,
                players,
                usesWhitelist() ? PlayerList.WHITELIST_FILE.toPath().toAbsolutePath() : null);
        try {
            Path dir = Handoff.start(spec);
            E4mcClient.LOGGER.info("Handed {} to a background server in {}", worldDir, dir);
        } catch (Exception e) {
            E4mcClient.LOGGER.error("Failed to hand {} to a background server", worldDir, e);
            try {
                Handoff.recordFailure(serversDir(), worldDir, e);
            } catch (Exception recordFailure) {
                E4mcClient.LOGGER.error("Failed to record the failure", recordFailure);
            }
        }
    }

    /** Whether shared worlds in this game use its whitelist; see PlayerListMixin. */
    private static boolean usesWhitelist() {
        return Config.INSTANCE.useWhiteList.value() && Config.INSTANCE.restoreDedicatedCommands.value();
    }

    /** The game's own -Xmx, so heavy modpacks get the memory they already need. */
    private static String maxHeap() {
        for (String arg : ManagementFactory.getRuntimeMXBean().getInputArguments()) {
            if (arg.startsWith("-Xmx")) {
                return arg.substring(4);
            }
        }
        return Runtime.getRuntime().maxMemory() / (1024 * 1024) + "M";
    }
}
