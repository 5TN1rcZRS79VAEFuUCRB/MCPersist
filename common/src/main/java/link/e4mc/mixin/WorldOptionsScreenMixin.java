package link.e4mc.mixin;

import link.e4mc.BackgroundServers;
import link.e4mc.E4mcClient;
import link.e4mc.WorldPersistence;
import net.minecraft.client.gui.components.CycleButton;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.layouts.LinearLayout;
import net.minecraft.client.gui.screens.WorldOptionsScreen;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.network.chat.Component;
import net.minecraft.world.level.storage.LevelResource;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.io.IOException;
import java.nio.file.Path;

/** Adds the per-world persistence toggle to the multiplayer (Open to LAN) options. */
@Mixin(WorldOptionsScreen.class)
public abstract class WorldOptionsScreenMixin {
    @Inject(method = "multiplayerOptions", at = @At("TAIL"))
    private void mcpersist$addPersistenceToggle(LinearLayout layout, IntegratedServer server, CallbackInfo ci) {
        Path worldDir = server.getWorldPath(LevelResource.ROOT);
        layout.addChild(CycleButton.onOffBuilder(WorldPersistence.isPersistent(worldDir))
                .withTooltip(value -> Tooltip.create(Component.translatable("options.mcpersist.persistent.tooltip")))
                // As wide as the two-button rows above: the label doesn't fit a standard button.
                .create(0, 0, 308, 20, Component.translatable("options.mcpersist.persistent"), (button, value) -> {
                    try {
                        WorldPersistence.setPersistent(worldDir, value);
                        if (!value) {
                            BackgroundServers.disable(worldDir);
                        }
                    } catch (IOException e) {
                        E4mcClient.LOGGER.error("Failed to save persistence setting for {}", worldDir, e);
                        button.setValue(!value);
                    }
                }));
    }
}
