package link.mcpersist.dialtone;

import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import io.netty.channel.*;
import io.netty.util.internal.StringUtil;
import link.e4mc.iroh.Connection;
import link.e4mc.iroh.Endpoint;
import link.e4mc.iroh.Native;
import link.e4mc.iroh.Stream;

import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;

public class DialtoneChannel extends AbstractChannel {
    private static final ChannelMetadata METADATA = new ChannelMetadata(false);
    private final ChannelConfig config = new DefaultChannelConfig(this);
    Endpoint endpoint;
    Connection connection;
    Stream stream;
    volatile boolean closed = false;
    AtomicBoolean readInFlight = new AtomicBoolean(false);
    AtomicBoolean writeInFlight = new AtomicBoolean(false);

    public DialtoneChannel() {
        super(null);
    }

    DialtoneChannel(DialtoneServerChannel parent) {
        super(parent);
        endpoint = parent.endpoint;
    }

    /** Keying material both ends of the connection derive alike, standing in for the server hash. */
    public byte[] authDigest() {
        return connection.exportKeyingMaterial("EXPERIMENTAL mojang authentication".getBytes(StandardCharsets.UTF_8), new byte[0], 20);
    }

    @Override
    protected AbstractUnsafe newUnsafe() {
        return new DialtoneUnsafe();
    }

    @Override
    protected boolean isCompatible(EventLoop loop) {
        return true;
    }

    @Override
    protected SocketAddress localAddress0() {
        return new DialtoneAddress(endpoint.address());
    }

    /** The relay address a joining player dialed; see {@link #remoteAddress0()}. */
    private InetSocketAddress dialed;

    // A joining player's end reports the address they dialed, not the peer's ticket: Simple
    // Voice Chat (and anything else asking where the server is) needs an IP address.
    @Override
    protected SocketAddress remoteAddress0() {
        return dialed != null ? dialed : new DialtoneAddress(connection.peerAddress());
    }

    @Override
    protected void doBind(SocketAddress localAddress) {
        throw new UnsupportedOperationException();
    }

    @Override
    protected void doDisconnect() {
        doClose();
    }

    @Override
    protected void doClose() {
        closed = true;
        stream.close();
        connection.close(0, new byte[0]);
        stream = null;
        connection = null;
        pipeline().fireChannelInactive();
    }

    @Override
    protected void doBeginRead() {
        if (!isActive()) {
            return;
        }
        if (readInFlight.compareAndExchange(false, true)) {
            return;
        }
        stream.readIrohStreamByteArray(65536).thenAccept(arr -> {
            readInFlight.set(false);
            if (arr == null) {
                if (!closed) {
                    doClose();
                }
                return;
            }
            pipeline().fireChannelRead(Unpooled.wrappedBuffer(arr));
            pipeline().fireChannelReadComplete();
        }).exceptionally(t -> {
            readInFlight.set(false);
            if (!closed) {
                pipeline().fireExceptionCaught(t);
            }
            return null;
        });
    }

    @Override
    protected void doWrite(ChannelOutboundBuffer in) {
        if (writeInFlight.compareAndExchange(false, true)) {
            return;
        }
        while (true) {
            Object msg = in.current();
            if (msg == null) {
                writeInFlight.set(false);
                break;
            }
            if (msg instanceof ByteBuf buf) {
                if (!buf.isReadable()) {
                    in.remove();
                    writeInFlight.set(false);
                    continue;
                }
                ByteBuffer byteBuffer = null;
                try {
                    byteBuffer = buf.nioBuffer();
                    if (!byteBuffer.isDirect()) {
                        byteBuffer = null;
                    }
                } catch (UnsupportedOperationException ignored) {
                }
                try {
                    if (byteBuffer == null) {
                        byte[] arr = new byte[buf.readableBytes()];
                        buf.readBytes(arr, 0, buf.readableBytes());
                        stream.writeIrohStreamByteArray(arr, 0, arr.length).thenAccept(nothing -> {
                            in.remove();
                            writeInFlight.set(false);
                        }).join();
                    } else {
                        assert byteBuffer.remaining() == buf.readableBytes();
                        stream.writeIrohStreamByteBuffer(byteBuffer, byteBuffer.position(), byteBuffer.remaining()).join();
                        in.remove();
                        writeInFlight.set(false);
                    }
                } catch (Throwable e) {
                    in.remove(e);
                    writeInFlight.set(false);
                }
            } else {
                in.remove(new UnsupportedOperationException(
                        "unsupported message type: " + StringUtil.simpleClassName(msg)));
                writeInFlight.set(false);
            }
        }
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
        return stream != null && !closed;
    }

    @Override
    public ChannelMetadata metadata() {
        return METADATA;
    }

    private class DialtoneUnsafe extends AbstractUnsafe {
        @Override
        public void connect(SocketAddress remoteAddress, SocketAddress localAddress, ChannelPromise promise) {
            if (!promise.setUncancellable() || !ensureOpen(promise)) {
                return;
            }

            try {
                if (remoteAddress instanceof DialtoneAddress dialtoneAddress) {
                    dialed = dialtoneAddress.dialed;
                    if (DialtoneAmbientSession.INSTANCE.endpoint == null) {
                        DialtoneAmbientSession.INSTANCE.start();
                    }
                    endpoint = DialtoneAmbientSession.INSTANCE.endpoint;
                    DialtoneAmbientSession.INSTANCE
                            .endpoint
                            .connect(dialtoneAddress.actualAddress, "e4mc-dialtone".getBytes(StandardCharsets.UTF_8))
                            .thenAccept(conn -> {
                                connection = conn;
                                conn.openBi().thenAccept(bidi -> {
                                    stream = bidi;
                                    pipeline().fireChannelActive();
                                    safeSetSuccess(promise);
                                }).exceptionally(t -> {
                                    safeSetFailure(promise, annotateConnectException(t, remoteAddress));
                                    return null;
                                });
                            }).exceptionally(t -> {
                                safeSetFailure(promise, annotateConnectException(t, remoteAddress));
                                return null;
                            });
                } else {
                    throw new UnsupportedOperationException();
                }
            } catch (Throwable t) {
                safeSetFailure(promise, annotateConnectException(t, remoteAddress));
            }
        }
    }
}
