package link.mcpersist.dialtone;

import io.netty.channel.AbstractServerChannel;
import io.netty.channel.ChannelConfig;
import io.netty.channel.DefaultChannelConfig;
import io.netty.channel.EventLoop;
import link.mcpersist.MCPersist;
import link.e4mc.iroh.Endpoint;
import link.e4mc.iroh.Resolvable;

import java.net.SocketAddress;

public class DialtoneServerChannel extends AbstractServerChannel {
    private final ChannelConfig config = new DefaultChannelConfig(this);
    Endpoint endpoint;
    Thread dispatcher;
    boolean closed = false;

    @Override
    protected boolean isCompatible(EventLoop loop) {
        return true;
    }

    @Override
    protected SocketAddress localAddress0() {
        return new DialtoneAddress(endpoint.address());
    }

    @Override
    protected void doBind(SocketAddress localAddress) throws Exception {
        this.endpoint = DialtoneAmbientSession.newEndpoint();
        this.dispatcher = DialtoneAmbientSession.dispatch(endpoint, "Dialtone Server Dispatcher");
        endpoint.watchAddress(new Resolvable<>() {
            @Override
            public void resolve(String addr) {
                if (addr != null) {
                    MCPersist.LOGGER.info("got new session ticket");
                    pipeline().fireUserEventTriggered(new DialtoneAddress(addr));
                }
            }

            @Override
            public void reject(Throwable throwable) {
            }
        });
    }

    @Override
    protected void doClose() {
        DialtoneAmbientSession.close(endpoint, dispatcher);
        endpoint = null;
        dispatcher = null;
        closed = true;
    }

    @Override
    protected void doBeginRead() throws Exception {
        endpoint.accept().thenAccept(preconn -> {
            MCPersist.LOGGER.info("preconn accepted, dialtone child registered");
            var channel = new DialtoneChannel(this);
            pipeline().fireChannelRead(channel);
            pipeline().fireChannelReadComplete();
            preconn.thenAccept(conn -> {
                MCPersist.LOGGER.info("conn accepted, dialtone child pre-active");
                channel.connection = conn;
                conn.acceptBi().thenAccept(bidi -> {
                    MCPersist.LOGGER.info("bidi accepted, dialtone child active");
                    channel.stream = bidi;
                    channel.pipeline().fireChannelActive();
                });
            }).exceptionally(e -> {
                channel.pipeline().fireChannelInactive();
                pipeline().fireExceptionCaught(e);
                return null;
            });
        }).exceptionally(e -> {
            pipeline().fireExceptionCaught(e);
            return null;
        });
    }

    @Override
    public ChannelConfig config() {
        return config;
    }

    @Override
    public boolean isOpen() {
        return !closed;
    }

    @Override
    public boolean isActive() {
        return endpoint != null;
    }
}
