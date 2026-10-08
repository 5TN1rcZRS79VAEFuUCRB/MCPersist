package link.mcpersist.mixin;

import link.mcpersist.DialtoneConnectionExtensions;
import link.mcpersist.ModCheck;
import net.minecraft.client.multiplayer.ClientHandshakePacketListenerImpl;
import net.minecraft.network.Connection;
import net.minecraft.network.protocol.login.ClientboundCustomQueryPacket;
import net.minecraft.network.protocol.login.ClientboundHelloPacket;
import net.minecraft.network.protocol.login.ServerboundCustomQueryAnswerPacket;
import net.minecraft.util.Crypt;
import net.minecraft.util.CryptException;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import javax.crypto.SecretKey;
import java.security.PublicKey;

@Mixin(ClientHandshakePacketListenerImpl.class)
public class ClientHandshakePacketListenerImplMixin {
    @Shadow @Final private Connection connection;

    /** Tells a server with requireMod that this client has MCPersist. */
    @Inject(method = "handleCustomQuery", at = @At("HEAD"), cancellable = true)
    private void mcpersist$answerModCheck(ClientboundCustomQueryPacket packet, CallbackInfo ci) {
        if (packet.payload().id().equals(ModCheck.ID)) {
            ci.cancel();
            connection.send(new ServerboundCustomQueryAnswerPacket(packet.transactionId(), ModCheck.ANSWER));
        }
    }

    @Redirect(method = "handleHello", at = @At(value = "INVOKE", target = "Lnet/minecraft/network/protocol/login/ClientboundHelloPacket;getPublicKey()Ljava/security/PublicKey;"))
    private PublicKey publicKey(ClientboundHelloPacket instance) throws CryptException {
        if (((DialtoneConnectionExtensions) connection).mcpersist$isDialtone()) {
            return null;
        }
        return instance.getPublicKey();
    }

    @Redirect(method = "handleHello", at = @At(value = "INVOKE", target = "Lnet/minecraft/util/Crypt;digestData(Ljava/lang/String;Ljava/security/PublicKey;Ljavax/crypto/SecretKey;)[B"))
    private byte[] digestData(String string, PublicKey publicKey, SecretKey secretKey) throws CryptException {
        if (((DialtoneConnectionExtensions) connection).mcpersist$isDialtone()) {
            return ((DialtoneConnectionExtensions) connection).mcpersist$authDigest();
        }
        return Crypt.digestData(string, publicKey, secretKey);
    }
}
