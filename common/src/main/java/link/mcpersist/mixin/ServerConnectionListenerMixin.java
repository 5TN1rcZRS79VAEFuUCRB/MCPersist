package link.mcpersist.mixin;

import io.netty.channel.ChannelHandler;
import io.netty.channel.EventLoopGroup;
import link.mcpersist.Config;
import link.mcpersist.MCPersist;
import link.mcpersist.QuiclimeSession;
import link.mcpersist.SessionPlayers;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.network.ServerConnectionListener;
import net.minecraft.world.level.storage.LevelResource;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.ModifyArg;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.net.InetAddress;

@Mixin(ServerConnectionListener.class)
public abstract class ServerConnectionListenerMixin {
    @Shadow
    @Final
    private MinecraftServer server;

    @Unique
    private ChannelHandler mcpersist$childHandler;
    @Unique
    private EventLoopGroup mcpersist$group;

    @ModifyArg(method = "startTcpServerListener", at = @At(value = "INVOKE", target = "Lio/netty/bootstrap/ServerBootstrap;childHandler(Lio/netty/channel/ChannelHandler;)Lio/netty/bootstrap/ServerBootstrap;", remap = false))
    private ChannelHandler interceptHandler(ChannelHandler childHandler) {
        mcpersist$childHandler = childHandler;
        return childHandler;
    }

    @ModifyArg(method = "startTcpServerListener", at = @At(value = "INVOKE", target = "Lio/netty/bootstrap/ServerBootstrap;group(Lio/netty/channel/EventLoopGroup;)Lio/netty/bootstrap/ServerBootstrap;", remap = false))
    private EventLoopGroup interceptGroup(EventLoopGroup group) {
        mcpersist$group = group;
        return group;
    }

    @Inject(method = "startTcpServerListener", at = @At(value = "TAIL"))
    private void interceptGroup(InetAddress inetAddress, int i, CallbackInfo ci) {
        // On an integrated server this is the host opening the world to LAN.
        ((SessionPlayers) server).mcpersist$markShared();
        ChannelHandler handler = mcpersist$childHandler;
        EventLoopGroup group = mcpersist$group;
        mcpersist$childHandler = null;
        mcpersist$group = null;
        if (Config.hostEnabled) {
            QuiclimeSession session = new QuiclimeSession(handler, group, server.getWorldPath(LevelResource.ROOT));
            MCPersist.session = session;
            if (server.isDedicatedServer()) {
                // A dedicated server listens before it's up, and drops logins until its first
                // tick; players the relay is holding for it would be handed over too early.
                new Thread(() -> {
                    awaitFirstTick(server);
                    session.startAsync();
                }, "MCPersist relay wait").start();
            } else {
                session.startAsync();
            }
        }
    }

    @Unique
    private static void awaitFirstTick(MinecraftServer server) {
        long deadline = System.currentTimeMillis() + 300_000;
        while (server.getStatus() == null && System.currentTimeMillis() < deadline) {
            try {
                Thread.sleep(100);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
        }
    }

    @Inject(method = "stop", at = @At(value = "HEAD"))
    private void interceptStop(CallbackInfo ci) {
        QuiclimeSession session = MCPersist.session;
        if ((session != null) && (session.state != QuiclimeSession.State.STOPPED)) {
            session.stop();
            MCPersist.session = null;
        }
    }
}
