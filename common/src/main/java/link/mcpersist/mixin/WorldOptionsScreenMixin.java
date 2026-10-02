package link.mcpersist.mixin;

import link.mcpersist.BackgroundServers;
import link.mcpersist.MCPersist;
import link.mcpersist.WorldPersistence;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.CycleButton;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.layouts.LinearLayout;
import net.minecraft.client.gui.screens.ConfirmScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.WorldOptionsScreen;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.network.chat.Component;
import net.minecraft.world.level.storage.LevelResource;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
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
                    if (!value) {
                        mcpersist$setPersistent(button, worldDir, false);
                        return;
                    }
                    // Starting at every login surprises people, so spell it out before turning it on.
                    // Coming back to this screen keeps its widgets and unapplied changes.
                    button.setValue(false);
                    Minecraft minecraft = Minecraft.getInstance();
                    Screen self = (Screen) (Object) this;
                    minecraft.gui.setScreen(new ConfirmScreen(confirmed -> {
                        if (confirmed) {
                            button.setValue(true);
                            mcpersist$setPersistent(button, worldDir, true);
                        }
                        minecraft.gui.setScreen(self);
                    }, Component.translatable("options.mcpersist.persistent.confirm.title"),
                            Component.translatable("options.mcpersist.persistent.tooltip")));
                }));
    }

    @Unique
    private static void mcpersist$setPersistent(CycleButton<Boolean> button, Path worldDir, boolean value) {
        try {
            WorldPersistence.setPersistent(worldDir, value);
            if (!value) {
                BackgroundServers.stop(worldDir, () -> {});
            }
        } catch (IOException e) {
            MCPersist.LOGGER.error("Failed to save persistence setting for {}", worldDir, e);
            button.setValue(!value);
        }
    }
}
