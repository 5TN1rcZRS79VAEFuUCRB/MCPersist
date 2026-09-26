package link.e4mc.mixin;

import link.e4mc.BackgroundServers;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.world.level.storage.LevelSummary;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * A world held by its own background server is locked, which makes the world list refuse to
 * play it before joinWorld (where it's redirected to that server) is ever reached.
 */
@Mixin(LevelSummary.class)
public abstract class LevelSummaryMixin {
    @Shadow
    public abstract String getLevelId();

    @Shadow
    public abstract boolean isLocked();

    @Inject(method = "primaryActionActive", at = @At("RETURN"), cancellable = true)
    private void mcpersist$joinableInBackground(CallbackInfoReturnable<Boolean> cir) {
        if (!cir.getReturnValueZ() && isLocked() && BackgroundServers.isRunning(getLevelId())) {
            cir.setReturnValue(true);
        }
    }

    /** Instead of "Locked by another running instance of Minecraft". Built once per list load. */
    @Inject(method = "createInfo", at = @At("HEAD"), cancellable = true)
    private void mcpersist$runningInBackgroundInfo(CallbackInfoReturnable<Component> cir) {
        if (isLocked() && BackgroundServers.isRunning(getLevelId())) {
            cir.setReturnValue(Component.translatable("selectWorld.mcpersist.runningInBackground")
                    .withStyle(ChatFormatting.GREEN));
        }
    }
}
