package link.e4mc.mixin;

import link.e4mc.BackgroundServers;
import link.e4mc.E4mcClient;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.worldselection.WorldSelectionList;
import net.minecraft.world.level.storage.LevelSummary;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.io.IOException;

/** Playing a world whose background server is running joins that server. */
@Mixin(WorldSelectionList.WorldListEntry.class)
public abstract class WorldListEntryMixin {
    @Shadow
    @Final
    private LevelSummary summary;

    @Shadow
    @Final
    private Screen screen;

    /** Locked by its own background server; checked once, since entries are rebuilt on reload. */
    @Unique
    private boolean mcpersist$inBackground;

    @Inject(method = "<init>", at = @At("TAIL"))
    private void mcpersist$checkBackground(CallbackInfo ci) {
        mcpersist$inBackground = summary.isLocked() && BackgroundServers.isRunning(summary.getLevelId());
    }

    // Otherwise hovering the icon shows the red "locked" mark and tooltip instead of play.
    @Redirect(method = "extractContent", at = @At(value = "INVOKE",
            target = "Lnet/minecraft/world/level/storage/LevelSummary;isLocked()Z"))
    private boolean mcpersist$notLockedWhenInBackground(LevelSummary summary) {
        return summary.isLocked() && !mcpersist$inBackground;
    }

    @Inject(method = "joinWorld", at = @At("HEAD"), cancellable = true)
    private void mcpersist$joinBackgroundServer(CallbackInfo ci) {
        String levelId = summary.getLevelId();
        if (!BackgroundServers.isRunning(levelId)) {
            return;
        }
        try {
            BackgroundServers.join(screen, levelId, summary.getLevelName());
            ci.cancel();
        } catch (IOException e) {
            E4mcClient.LOGGER.error("Failed to join the background server for {}", levelId, e);
        }
    }
}
