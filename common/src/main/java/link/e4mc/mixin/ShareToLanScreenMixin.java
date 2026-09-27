package link.e4mc.mixin;

import link.e4mc.BackgroundServers;
import link.e4mc.E4mcClient;
import link.e4mc.WorldPersistence;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.CycleButton;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.ShareToLanScreen;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.network.chat.Component;
import net.minecraft.world.level.storage.LevelResource;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.io.IOException;
import java.nio.file.Path;

/** Adds the per-world persistence toggle to the Open to LAN screen. */
@Mixin(ShareToLanScreen.class)
public abstract class ShareToLanScreenMixin extends Screen {
    protected ShareToLanScreenMixin(Component title) {
        super(title);
    }

    @Inject(method = "init", at = @At("TAIL"))
    private void mcpersist$addPersistenceToggle(CallbackInfo ci) {
        IntegratedServer server = Minecraft.getInstance().getSingleplayerServer();
        if (server == null) {
            return;
        }
        Path worldDir = server.getWorldPath(LevelResource.ROOT);
        // Between the game mode row (y 100-120) and the port label (y 142).
        addRenderableWidget(CycleButton.onOffBuilder(WorldPersistence.isPersistent(worldDir))
                .withTooltip(value -> Tooltip.create(Component.translatable("options.mcpersist.persistent.tooltip")))
                // As wide as the two-button row above: the label doesn't fit a standard button.
                .create(width / 2 - 155, 121, 310, 20, Component.translatable("options.mcpersist.persistent"), (button, value) -> {
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
