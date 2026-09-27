package link.e4mc.mixin;

import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import io.netty.bootstrap.Bootstrap;
import io.netty.channel.*;
import link.e4mc.DialtoneConnectionExtensions;
import link.e4mc.SmugglersInetSocketAddress;
import link.e4mc.dialtone.DialtoneAddress;
import link.e4mc.dialtone.DialtoneAmbientSession;
import link.e4mc.dialtone.DialtoneChannel;
import net.minecraft.network.Connection;
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
import java.net.SocketAddress;

@Mixin(Connection.class)
public abstract class ConnectionMixin implements DialtoneConnectionExtensions {

    @Shadow private Channel channel;
    @Shadow private SocketAddress address;
    @Unique
    private SocketAddress e4mc$dialedAddress;
    @Unique
    private static DialtoneAddress e4mc$smuggledDialtoneAddress = null;

    @Override
    public boolean e4mc$isDialtone() {
        return channel instanceof DialtoneChannel;
    }

    @Override
    public void e4mc$setDialedAddress(SocketAddress address) {
        e4mc$dialedAddress = address;
    }

    // A Dialtone channel's own remote address is its ticket; Simple Voice Chat (and anything
    // else asking where the server is) needs the relay address the player dialed.
    @Inject(method = "channelActive", at = @At("TAIL"))
    private void e4mc$reportDialedAddress(ChannelHandlerContext ctx, CallbackInfo ci) {
        if (e4mc$dialedAddress != null) {
            address = e4mc$dialedAddress;
        }
    }

    @Override
    public byte[] e4mc$exportKeyingMaterial(byte[] label, byte[] context, int length) {
        if (channel instanceof DialtoneChannel dialtoneChannel) {
            return dialtoneChannel.exportKeyingMaterial(label, context, length);
        }
        return null;
    }

    @Inject(method = "connect", at = @At("HEAD"))
    private static void hijackStart(InetSocketAddress inetSocketAddress, boolean useEpoll, Connection connection, CallbackInfoReturnable<ChannelFuture> cir) {
        if (inetSocketAddress instanceof SmugglersInetSocketAddress smuggledAddress) {
            e4mc$smuggledDialtoneAddress = new DialtoneAddress(smuggledAddress.ticket);
            ((DialtoneConnectionExtensions) connection).e4mc$setDialedAddress(smuggledAddress);
        }
    }

    @ModifyArg(method = "connect", at = @At(value = "INVOKE", target = "Lio/netty/bootstrap/Bootstrap;channel(Ljava/lang/Class;)Lio/netty/bootstrap/AbstractBootstrap;"))
    private static Class hijackChannel(Class clazz) {
        if (e4mc$smuggledDialtoneAddress != null) {
            return DialtoneChannel.class;
        } else {
            return clazz;
        }
    }

    @ModifyArg(method = "connect", at = @At(value = "INVOKE", target = "Lio/netty/bootstrap/Bootstrap;group(Lio/netty/channel/EventLoopGroup;)Lio/netty/bootstrap/AbstractBootstrap;"))
    private static EventLoopGroup hijackGroup(EventLoopGroup group) {
        if (e4mc$smuggledDialtoneAddress != null) {
            return DialtoneAmbientSession.INSTANCE.group;
        } else {
            return group;
        }
    }

    @WrapOperation(method = "connect", at = @At(value = "INVOKE", target = "Lio/netty/bootstrap/Bootstrap;connect(Ljava/net/InetAddress;I)Lio/netty/channel/ChannelFuture;"))
    private static ChannelFuture hijackConnect(Bootstrap instance, InetAddress inetHost, int inetPort, Operation<ChannelFuture> operation) {
        if (e4mc$smuggledDialtoneAddress != null) {
            var ret = instance.connect(e4mc$smuggledDialtoneAddress);
            e4mc$smuggledDialtoneAddress = null;
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
