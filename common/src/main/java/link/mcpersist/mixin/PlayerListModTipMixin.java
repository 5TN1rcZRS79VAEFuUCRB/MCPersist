package link.mcpersist.mixin;

import link.mcpersist.DialtoneConnectionExtensions;
import link.mcpersist.ModCheck;
import net.minecraft.network.Connection;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.CommonListenerCookie;
import net.minecraft.server.players.PlayerList;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Suggests MCPersist to a player who joined through the relay without it. */
@Mixin(PlayerList.class)
public class PlayerListModTipMixin {
    @Inject(method = "placeNewPlayer", at = @At("TAIL"))
    private void mcpersist$suggestMod(Connection connection, ServerPlayer player, CommonListenerCookie cookie, CallbackInfo ci) {
        if (((DialtoneConnectionExtensions) connection).mcpersist$withoutMod()) {
            player.sendSystemMessage(ModCheck.TIP);
        }
    }
}
