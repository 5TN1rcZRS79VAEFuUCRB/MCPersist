package link.e4mc.mixin;

import link.e4mc.BackgroundServers;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.gui.screens.worldselection.SelectWorldScreen;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.network.chat.Component;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Leaving a world's background server returns to the world list, where it was joined from. */
@Mixin(Minecraft.class)
public abstract class MinecraftMixin {
    @Shadow
    public abstract ServerData getCurrentServer();

    @Unique
    private boolean mcpersist$leavingBackground;

    @Inject(method = "disconnectFromWorld", at = @At("HEAD"))
    private void mcpersist$noteBackground(Component reason, CallbackInfo ci) {
        ServerData server = getCurrentServer();
        mcpersist$leavingBackground = server != null && server == BackgroundServers.joined;
    }

    @Inject(method = "disconnectFromWorld", at = @At("TAIL"))
    private void mcpersist$backToWorldList(Component reason, CallbackInfo ci) {
        if (mcpersist$leavingBackground) {
            mcpersist$leavingBackground = false;
            ((Minecraft) (Object) this).gui.setScreen(new SelectWorldScreen(new TitleScreen()));
        }
    }
}
