package link.e4mc.mixin;

import link.e4mc.BackgroundServers;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.TitleScreen;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Reports background-server problems when the game starts. */
@Mixin(TitleScreen.class)
public abstract class TitleScreenMixin {
    @Inject(method = "init", at = @At("TAIL"))
    private void mcpersist$showProblems(CallbackInfo ci) {
        BackgroundServers.showProblemsOnce((Screen) (Object) this);
    }
}
