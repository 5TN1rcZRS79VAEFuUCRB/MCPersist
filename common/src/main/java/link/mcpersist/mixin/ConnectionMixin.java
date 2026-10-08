package link.mcpersist.mixin;

import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import io.netty.bootstrap.Bootstrap;
import io.netty.channel.*;
import io.netty.incubator.codec.quic.QuicStreamChannel;
import link.mcpersist.DialtoneConnectionExtensions;
import link.mcpersist.SmugglersInetSocketAddress;
import link.mcpersist.dialtone.DialtoneAddress;
import link.mcpersist.dialtone.DialtoneAmbientSession;
import link.mcpersist.dialtone.DialtoneChannel;
import net.minecraft.network.Connection;
import net.minecraft.server.network.EventLoopGroupHolder;
import org.objectweb.asm.Opcodes;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import javax.crypto.Cipher;
import java.net.InetAddress;
import java.net.InetSocketAddress;

@Mixin(Connection.class)
public abstract class ConnectionMixin implements DialtoneConnectionExtensions {

    @Shadow private Channel channel;
    @Unique
    private static DialtoneAddress mcpersist$smuggledDialtoneAddress = null;

    @Override
    public boolean mcpersist$isDialtone() {
        return channel instanceof DialtoneChannel;
    }

    @Unique
    private volatile boolean mcpersist$withoutMod;

    @Override
    public boolean mcpersist$isRelayed() {
        return channel instanceof QuicStreamChannel;
    }

    @Override
    public boolean mcpersist$withoutMod() {
        return mcpersist$withoutMod;
    }

    @Override
    public void mcpersist$markWithoutMod() {
        mcpersist$withoutMod = true;
    }

    @Override
    public byte[] mcpersist$authDigest() {
        if (channel instanceof DialtoneChannel dialtoneChannel) {
            return dialtoneChannel.authDigest();
        }
        return null;
    }

    @Inject(method = "connect", at = @At("HEAD"))
    private static void hijackStart(InetSocketAddress inetSocketAddress, EventLoopGroupHolder groups, Connection connection, CallbackInfoReturnable<ChannelFuture> cir) {
        if (inetSocketAddress instanceof SmugglersInetSocketAddress smuggledAddress) {
            mcpersist$smuggledDialtoneAddress = new DialtoneAddress(smuggledAddress.ticket, smuggledAddress);
        }
    }

    @ModifyArg(method = "connect", at = @At(value = "INVOKE", target = "Lio/netty/bootstrap/Bootstrap;channel(Ljava/lang/Class;)Lio/netty/bootstrap/AbstractBootstrap;"))
    private static Class hijackChannel(Class clazz) {
        if (mcpersist$smuggledDialtoneAddress != null) {
            return DialtoneChannel.class;
        } else {
            return clazz;
        }
    }

    @ModifyArg(method = "connect", at = @At(value = "INVOKE", target = "Lio/netty/bootstrap/Bootstrap;group(Lio/netty/channel/EventLoopGroup;)Lio/netty/bootstrap/AbstractBootstrap;"))
    private static EventLoopGroup hijackGroup(EventLoopGroup group) {
        if (mcpersist$smuggledDialtoneAddress != null) {
            return DialtoneAmbientSession.INSTANCE.group;
        } else {
            return group;
        }
    }

    @WrapOperation(method = "connect", at = @At(value = "INVOKE", target = "Lio/netty/bootstrap/Bootstrap;connect(Ljava/net/InetAddress;I)Lio/netty/channel/ChannelFuture;"))
    private static ChannelFuture hijackConnect(Bootstrap instance, InetAddress inetHost, int inetPort, Operation<ChannelFuture> operation) {
        if (mcpersist$smuggledDialtoneAddress != null) {
            var ret = instance.connect(mcpersist$smuggledDialtoneAddress);
            mcpersist$smuggledDialtoneAddress = null;
            return ret;
        } else {
            return operation.call(instance, inetHost, inetPort);
        }
    }

    @Inject(method = "setEncryptionKey", at = @At(value = "FIELD", target = "Lnet/minecraft/network/Connection;channel:Lio/netty/channel/Channel;", opcode = Opcodes.GETFIELD, ordinal = 0), cancellable = true)
    private void killDoubleEncryption(Cipher cipher, Cipher cipher2, CallbackInfo ci) {
        if (channel instanceof DialtoneChannel) {
            ci.cancel();
        }
    }
}
