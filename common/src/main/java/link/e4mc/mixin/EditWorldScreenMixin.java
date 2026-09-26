package link.e4mc.mixin;

import it.unimi.dsi.fastutil.booleans.BooleanConsumer;
import link.e4mc.E4mcClient;
import link.e4mc.WorldPersistence;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.layouts.LinearLayout;
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
public abstract class EditWorldScreenMixin {
    @Shadow
    @Final
    private LinearLayout layout;

    @Inject(method = "<init>", at = @At("TAIL"))
    private void mcpersist$addResetAddress(Minecraft minecraft, LevelStorageSource.LevelStorageAccess levelAccess, String name, BooleanConsumer callback, CallbackInfo ci) {
        Path worldDir = levelAccess.getLevelPath(LevelResource.ROOT);
        if (!WorldPersistence.hasKey(worldDir)) {
            return;
        }
        Screen self = (Screen) (Object) this;
        layout.addChild(Button.builder(Component.translatable("selectWorld.mcpersist.resetAddress"), button ->
                minecraft.gui.setScreen(new ConfirmScreen(confirmed -> {
                    if (confirmed) {
                        try {
                            WorldPersistence.resetKey(worldDir);
                        } catch (IOException e) {
                            E4mcClient.LOGGER.error("Failed to reset the address of {}", worldDir, e);
                        }
                    }
                    minecraft.gui.setScreen(self);
                }, Component.translatable("selectWorld.mcpersist.resetAddress.title"),
                        Component.translatable("selectWorld.mcpersist.resetAddress.message")))
        ).width(200).build());
    }
}
