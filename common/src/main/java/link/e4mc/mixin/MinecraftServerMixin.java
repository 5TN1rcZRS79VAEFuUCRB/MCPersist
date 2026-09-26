package link.e4mc.mixin;

import link.e4mc.Agnos;
import link.e4mc.E4mcClient;
import link.e4mc.WorldPersistence;
import link.e4mc.handoff.Handoff;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.level.storage.LevelResource;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.lang.management.ManagementFactory;
import java.nio.file.Path;

/** When the host leaves a persistent world, hands it to a background server. */
@Mixin(MinecraftServer.class)
public abstract class MinecraftServerMixin {
    @Shadow
    public abstract boolean isDedicatedServer();

    @Shadow
    public abstract Path getWorldPath(LevelResource resource);

    // At the end of stopServer the world is saved and its storage (and session.lock) closed.
    @Inject(method = "stopServer", at = @At("TAIL"))
    private void mcpersist$handOff(CallbackInfo ci) {
        if (isDedicatedServer()) {
            return;
        }
        Path worldDir = getWorldPath(LevelResource.ROOT).toAbsolutePath().normalize();
        if (!WorldPersistence.isPersistent(worldDir)) {
            return;
        }
        Path gameDir = Agnos.gameDir();
        Handoff.Spec spec = new Handoff.Spec(
                worldDir,
                gameDir.resolve("mods"),
                Agnos.configDir(),
                gameDir.resolve("mcpersist").resolve("servers"),
                Agnos.modVersion("minecraft"),
                Agnos.modVersion("fabricloader"),
                ProcessHandle.current().info().command().map(Path::of).orElseThrow(),
                maxHeap());
        // Not a daemon: if the game is closing, the handoff still finishes.
        new Thread(() -> {
            try {
                Path dir = Handoff.start(spec);
                E4mcClient.LOGGER.info("Handed {} to a background server in {}", worldDir, dir);
            } catch (Exception e) {
                E4mcClient.LOGGER.error("Failed to hand {} to a background server", worldDir, e);
            }
        }, "mcpersist-handoff").start();
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
