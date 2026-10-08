package link.mcpersist.mixin;

import com.mojang.authlib.GameProfile;
import link.mcpersist.Config;
import link.mcpersist.DialtoneConnectionExtensions;
import link.mcpersist.ModCheck;
import link.mcpersist.dialtone.DialtoneAddress;
import net.minecraft.network.Connection;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.login.ClientboundCustomQueryPacket;
import net.minecraft.network.protocol.login.ServerboundCustomQueryAnswerPacket;
import net.minecraft.network.protocol.login.ServerboundKeyPacket;
import net.minecraft.server.network.ServerLoginPacketListenerImpl;
import net.minecraft.util.Crypt;
import net.minecraft.util.CryptException;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import java.security.Key;
import java.security.PrivateKey;
import java.security.PublicKey;

@Mixin(ServerLoginPacketListenerImpl.class)
public class ServerLoginPacketListenerImplMixin {
    @Shadow @Final
    Connection connection;

    @Shadow
    public void disconnect(Component reason) {}

    @Unique
    private volatile boolean mcpersist$asked;
    @Unique
    private volatile boolean mcpersist$answered;

    /**
     * Asks whether the player's client has MCPersist: with requireMod, to turn away players
     * without it; otherwise only through the relay, to suggest it to them once they're in.
     * Holds the authenticated player at the last login step, which the game retries every
     * tick, until the answer comes. A client that never answers runs into the game's own 30 s
     * login timeout.
     */
    @Inject(method = "verifyLoginAndFinishConnectionSetup", at = @At("HEAD"), cancellable = true)
    private void mcpersist$askForMod(GameProfile profile, CallbackInfo ci) {
        if (mcpersist$answered || !(Config.requireMod
                || Config.dialtoneHostEnabled && ((DialtoneConnectionExtensions) connection).mcpersist$isRelayed())) {
            return;
        }
        ci.cancel();
        if (!mcpersist$asked) {
            mcpersist$asked = true;
            connection.send(new ClientboundCustomQueryPacket(ModCheck.TRANSACTION, ModCheck.QUERY));
        }
    }

    @Inject(method = "handleCustomQueryPacket", at = @At("HEAD"), cancellable = true)
    private void mcpersist$modAnswer(ServerboundCustomQueryAnswerPacket packet, CallbackInfo ci) {
        if (!mcpersist$asked || packet.transactionId() != ModCheck.TRANSACTION) {
            return;
        }
        ci.cancel();
        if (packet.payload() == null) {
            if (Config.requireMod) {
                disconnect(ModCheck.REFUSED);
                return;
            }
            ((DialtoneConnectionExtensions) connection).mcpersist$markWithoutMod();
        }
        mcpersist$answered = true;
    }

    @Redirect(method = "handleHello", at = @At(value = "INVOKE", target = "Ljava/security/PublicKey;getEncoded()[B"))
    private byte[] killDoubleEncryption(PublicKey instance) {
        if (connection.getRemoteAddress() instanceof DialtoneAddress) {
            return new byte[0];
        }
        return instance.getEncoded();
    }

    @Redirect(method = "handleKey", at = @At(value = "INVOKE", target = "Lnet/minecraft/network/protocol/login/ServerboundKeyPacket;isChallengeValid([BLjava/security/PrivateKey;)Z"))
    private boolean isChallengeValid(ServerboundKeyPacket instance, byte[] bs, PrivateKey privateKey) {
        if (connection.getRemoteAddress() instanceof DialtoneAddress) {
            return true;
        }
        return instance.isChallengeValid(bs, privateKey);
    }

    @Redirect(method = "handleKey", at = @At(value = "INVOKE", target = "Lnet/minecraft/network/protocol/login/ServerboundKeyPacket;getSecretKey(Ljava/security/PrivateKey;)Ljavax/crypto/SecretKey;"))
    private SecretKey getSecretKey(ServerboundKeyPacket instance, PrivateKey privateKey) throws CryptException {
        if (connection.getRemoteAddress() instanceof DialtoneAddress) {
            return null;
        }
        return instance.getSecretKey(privateKey);
    }

    @Redirect(method = "handleKey", at = @At(value = "INVOKE", target = "Lnet/minecraft/util/Crypt;getCipher(ILjava/security/Key;)Ljavax/crypto/Cipher;"))
    private Cipher getCipher(int i, Key key) throws CryptException {
        if (connection.getRemoteAddress() instanceof DialtoneAddress) {
            return null;
        }
        return Crypt.getCipher(i, key);
    }

    @Redirect(method = "handleKey", at = @At(value = "INVOKE", target = "Lnet/minecraft/util/Crypt;digestData(Ljava/lang/String;Ljava/security/PublicKey;Ljavax/crypto/SecretKey;)[B"))
    private byte[] digestData(String string, PublicKey publicKey, SecretKey secretKey) throws CryptException {
        if (connection.getRemoteAddress() instanceof DialtoneAddress) {
            return ((DialtoneConnectionExtensions) connection).mcpersist$authDigest();
        }
        return Crypt.digestData(string, publicKey, secretKey);
    }
}
