package link.e4mc.mixin;

import link.e4mc.BackgroundServers;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.PauseScreen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.gui.screens.worldselection.SelectWorldScreen;
import net.minecraft.client.multiplayer.ServerData;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Leaving a world's background server returns to the world list, where it was joined from.
 * On 1.20.1 the pause screen's Disconnect picks the next screen (the server list).
 */
@Mixin(PauseScreen.class)
public abstract class PauseScreenMixin {
    @Unique
    private boolean mcpersist$leavingBackground;

    @Inject(method = "onDisconnect", at = @At("HEAD"))
    private void mcpersist$noteBackground(CallbackInfo ci) {
        ServerData server = Minecraft.getInstance().getCurrentServer();
        mcpersist$leavingBackground = server != null && server == BackgroundServers.joined;
    }

    @Inject(method = "onDisconnect", at = @At("TAIL"))
    private void mcpersist$backToWorldList(CallbackInfo ci) {
        if (mcpersist$leavingBackground) {
            mcpersist$leavingBackground = false;
            Minecraft.getInstance().setScreen(new SelectWorldScreen(new TitleScreen()));
        }
    }
}
