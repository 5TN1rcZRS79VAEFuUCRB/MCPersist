package link.e4mc.mixin;

import link.e4mc.BackgroundServers;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.worldselection.SelectWorldScreen;
import net.minecraft.network.chat.Component;
import net.minecraft.world.level.storage.LevelSummary;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** "Stop Background Server" for the selected world, while its background server runs. */
@Mixin(SelectWorldScreen.class)
public abstract class SelectWorldScreenMixin extends Screen {
    @Unique
    private Button mcpersist$stopButton;
    @Unique
    private String mcpersist$selected;

    protected SelectWorldScreenMixin(Component title) {
        super(title);
    }

    @Inject(method = "init", at = @At("TAIL"))
    private void mcpersist$addStopButton(CallbackInfo ci) {
        mcpersist$stopButton = addRenderableWidget(Button.builder(
                Component.translatable("selectWorld.mcpersist.stopBackgroundServer"), button -> {
                    button.active = false;
                    String levelId = mcpersist$selected;
                    BackgroundServers.stop(levelId, () -> mcpersist$refresh(levelId));
                }).bounds(width - 160, 6, 150, 20).build());
        mcpersist$refresh(mcpersist$selected);
    }

    @Inject(method = "updateButtonStatus", at = @At("TAIL"))
    private void mcpersist$updateStopButton(LevelSummary summary, CallbackInfo ci) {
        mcpersist$refresh(summary == null ? null : summary.getLevelId());
    }

    @Unique
    private void mcpersist$refresh(String levelId) {
        mcpersist$selected = levelId;
        if (mcpersist$stopButton != null) {
            mcpersist$stopButton.active = levelId != null && BackgroundServers.isRunning(levelId);
        }
    }
}
