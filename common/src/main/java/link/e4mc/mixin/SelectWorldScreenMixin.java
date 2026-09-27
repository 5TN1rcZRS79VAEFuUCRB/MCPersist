package link.e4mc.mixin;

import link.e4mc.BackgroundServers;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.worldselection.SelectWorldScreen;
import net.minecraft.client.gui.screens.worldselection.WorldSelectionList;
import net.minecraft.network.chat.Component;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** "Stop Background Server" for the selected world, while its background server runs. */
@Mixin(SelectWorldScreen.class)
public abstract class SelectWorldScreenMixin extends Screen {
    @Shadow
    private WorldSelectionList list;

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
                    BackgroundServers.stop(levelId, () -> {
                        // Entries show the world as running until they're rebuilt.
                        ((WorldSelectionListAccessor) list).mcpersist$reloadWorldList();
                        mcpersist$refresh(levelId);
                    });
                }).bounds(width - 160, 6, 150, 20).build());
        mcpersist$refresh(mcpersist$selected);
    }

    // The list has already stored the new selection when it calls this.
    @Inject(method = "updateButtonStatus", at = @At("TAIL"))
    private void mcpersist$updateStopButton(boolean playable, boolean selected, CallbackInfo ci) {
        mcpersist$refresh(list == null ? null : list.getSelectedOpt()
                .map(entry -> ((WorldListEntryAccessor) (Object) entry).mcpersist$summary().getLevelId())
                .orElse(null));
    }

    @Unique
    private void mcpersist$refresh(String levelId) {
        mcpersist$selected = levelId;
        if (mcpersist$stopButton != null) {
            mcpersist$stopButton.active = levelId != null && BackgroundServers.isRunning(levelId);
        }
    }
}
