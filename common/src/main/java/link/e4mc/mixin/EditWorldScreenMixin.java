package link.e4mc.mixin;

import link.e4mc.E4mcClient;
import link.e4mc.WorldPersistence;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.ConfirmScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.worldselection.EditWorldScreen;
import net.minecraft.network.chat.Component;
import net.minecraft.world.level.storage.LevelResource;
import net.minecraft.world.level.storage.LevelStorageSource;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.io.IOException;
import java.nio.file.Path;

/** Adds "Reset MCPersist Address" to Edit World, for worlds that have an address. */
@Mixin(EditWorldScreen.class)
public abstract class EditWorldScreenMixin extends Screen {
    @Shadow
    @Final
    private LevelStorageSource.LevelStorageAccess levelAccess;

    protected EditWorldScreenMixin(Component title) {
        super(title);
    }

    @Inject(method = "init", at = @At("TAIL"))
    private void mcpersist$addResetAddress(CallbackInfo ci) {
        Path worldDir = levelAccess.getLevelPath(LevelResource.ROOT);
        if (!WorldPersistence.hasKey(worldDir)) {
            return;
        }
        Screen self = this;
        // The free row between Optimize World and Save.
        addRenderableWidget(Button.builder(Component.translatable("selectWorld.mcpersist.resetAddress"), button ->
                minecraft.setScreen(new ConfirmScreen(confirmed -> {
                    if (confirmed) {
                        try {
                            WorldPersistence.resetKey(worldDir);
                        } catch (IOException e) {
                            E4mcClient.LOGGER.error("Failed to reset the address of {}", worldDir, e);
                        }
                    }
                    minecraft.setScreen(self);
                }, Component.translatable("selectWorld.mcpersist.resetAddress.title"),
                        Component.translatable("selectWorld.mcpersist.resetAddress.message")))
        ).bounds(width / 2 - 100, height / 4 + 120 + 5, 200, 20).build());
    }
}
