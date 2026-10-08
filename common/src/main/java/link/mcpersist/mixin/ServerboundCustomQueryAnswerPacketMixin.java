package link.mcpersist.mixin;

import link.mcpersist.ModCheck;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.protocol.login.ServerboundCustomQueryAnswerPacket;
import net.minecraft.network.protocol.login.custom.CustomQueryAnswerPayload;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * The game reads every query answer as present, even a client's "I don't know this query", so a
 * server couldn't tell a client without MCPersist from one with it. Reads the answer to
 * MCPersist's query as it was sent.
 */
@Mixin(ServerboundCustomQueryAnswerPacket.class)
public class ServerboundCustomQueryAnswerPacketMixin {
    @Inject(method = "readPayload", at = @At("HEAD"), cancellable = true)
    private static void mcpersist$readModCheck(int transactionId, FriendlyByteBuf buf, CallbackInfoReturnable<CustomQueryAnswerPayload> cir) {
        if (transactionId == ModCheck.TRANSACTION) {
            boolean present = buf.readBoolean();
            buf.skipBytes(buf.readableBytes());
            cir.setReturnValue(present ? ModCheck.ANSWER : null);
        }
    }
}
