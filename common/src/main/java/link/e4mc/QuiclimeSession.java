package link.e4mc;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import io.netty.bootstrap.Bootstrap;
import io.netty.bootstrap.ServerBootstrap;
import io.netty.buffer.ByteBuf;
import io.netty.channel.*;
import io.netty.channel.epoll.EpollDatagramChannel;
import io.netty.channel.epoll.EpollEventLoopGroup;
import io.netty.channel.epoll.EpollIoHandler;
import io.netty.channel.kqueue.KQueueDatagramChannel;
import io.netty.channel.kqueue.KQueueEventLoopGroup;
import io.netty.channel.kqueue.KQueueIoHandler;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.nio.NioIoHandler;
import io.netty.channel.socket.DatagramChannel;
import io.netty.channel.socket.nio.NioDatagramChannel;
import io.netty.handler.codec.ByteToMessageCodec;
import io.netty.incubator.codec.quic.*;
import link.e4mc.dialtone.DialtoneAddress;
import link.e4mc.dialtone.DialtoneServerChannel;
import link.e4mc.iroh.Endpoint;
import net.minecraft.ChatFormatting;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.HoverEvent;
import net.minecraft.network.chat.MutableComponent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Consumer;

public class QuiclimeSession {
    private static final Gson gson = new Gson();
    private static final Logger LOGGER = LoggerFactory.getLogger(E4mcClient.MOD_ID);
    final ChannelHandler handler;

    private static class ControlMessageCodec extends ByteToMessageCodec<ControlMessageCodec.ControlMessage> {
        public ControlMessageCodec() {
            super();
        }

        public interface ControlMessage {}

        public static class ProbeCapabilitiesMessageServerbound implements ControlMessage {
            String kind = "probe_capabilities";
            public ProbeCapabilitiesMessageServerbound() {}
        }

        public static class RequestDomainAssignmentMessageServerbound implements ControlMessage {
            String kind = "request_domain_assignment";
            // A persistent world's key; the relay gives it the same domain every time.
            // Null (omitted from the JSON) for a random domain, as in e4mc.
            String key;
            public RequestDomainAssignmentMessageServerbound(String key) {
                this.key = key;
            }
        }

        public static class DialtoneRegisterTicketMessageServerbound implements ControlMessage {
            String kind = "dialtone_register_ticket";
            String ticket;
            public DialtoneRegisterTicketMessageServerbound(String ticket) {
                this.ticket = ticket;
            }
        }

        public static class HandingOffMessageServerbound implements ControlMessage {
            String kind = "handing_off";
        }

        public static class HandedOffMessageClientbound implements ControlMessage {
            String kind = "handed_off";
        }

        public static class DomainAssignmentCompleteMessageClientbound implements ControlMessage {
            String kind = "domain_assignment_complete";
            String domain;
            public DomainAssignmentCompleteMessageClientbound(String domain) {
                this.domain = domain;
            }
        }

        public static class DomainAssignmentFailedMessageClientbound implements ControlMessage {
            String kind = "domain_assignment_failed";
            String reason;
            public DomainAssignmentFailedMessageClientbound(String reason) {
                this.reason = reason;
            }
        }

        public static class RequestMessageBroadcastMessageClientbound implements ControlMessage {
            String kind = "request_message_broadcast";
            String message;
            public RequestMessageBroadcastMessageClientbound(String message) {
                this.message = message;
            }
        }

        public static class HasCapabilitiesMessageClientbound implements ControlMessage {
            String kind = "has_capabilities";
            String[] caps;
            public HasCapabilitiesMessageClientbound(String[] caps) {
                this.caps = caps;
            }
        }

        public static class TicketRegisteredMessageClientbound implements ControlMessage {
            String kind = "ticket_registered";
            public TicketRegisteredMessageClientbound() {
            }
        }

        public static class UnknownMessageMessageClientbound implements ControlMessage {
            String kind = "ticket_registered";
            public UnknownMessageMessageClientbound() {
            }
        }

        @Override
        protected void encode(ChannelHandlerContext ctx, ControlMessage msg, ByteBuf out) {
            E4mcClient.LOGGER.info("writing {}", msg);
            try {
                byte[] json = gson.toJson(msg).getBytes(StandardCharsets.UTF_8);
                writeVarInt(out, json.length);
                out.writeBytes(json);
            } catch (Throwable e) {
                E4mcClient.LOGGER.error("weird", e);
            }
            E4mcClient.LOGGER.info("writing {} bytes", out.readableBytes());
        }

        @Override
        protected void decode(ChannelHandlerContext ctx, ByteBuf in, List<Object> out) {
            int size = in.getByte(in.readerIndex());
            if (in.readableBytes() >= size + 1) {
                in.skipBytes(1);
                var buf = new byte[size];
                in.readBytes(buf);
                var json = gson.fromJson(new String(buf, StandardCharsets.UTF_8), JsonObject.class);
                switch (json.get("kind").getAsString()) {
                    case "domain_assignment_complete":
                        out.add(gson.fromJson(json, DomainAssignmentCompleteMessageClientbound.class));
                        break;
                    case "domain_assignment_failed":
                        out.add(gson.fromJson(json, DomainAssignmentFailedMessageClientbound.class));
                        break;
                    case "request_message_broadcast":
                        out.add(gson.fromJson(json, RequestMessageBroadcastMessageClientbound.class));
                        break;
                    case "has_capabilities":
                        out.add(gson.fromJson(json, HasCapabilitiesMessageClientbound.class));
                        break;
                    case "ticket_registered":
                        out.add(gson.fromJson(json, TicketRegisteredMessageClientbound.class));
                        break;
                    case "handed_off":
                        out.add(new HandedOffMessageClientbound());
                        break;
                    case "unknown_message":
                        out.add(gson.fromJson(json, UnknownMessageMessageClientbound.class));
                        break;
                    default:
                        throw new RuntimeException("Invalid message type!");
                }
            }
        }
    }

    public State state = State.STARTING;
    public Throwable failureCause = null;
    public enum State {
        STARTING,
        STARTED,
        UNHEALTHY,
        STOPPING,
        STOPPED
    }

    final EventLoopGroup group;
    private DatagramChannel datagramChannel;
    private QuicChannel quicChannel;

    private DialtoneServerChannel dialtoneChannel;
    final String worldKey;
    /** The address the relay assigned, once assigned. */
    public volatile String domain;
    private volatile QuicStreamChannel controlChannel;
    private final CompletableFuture<Void> handedOff = new CompletableFuture<>();

    /** {@code worldKey} is the world's key if it's persistent, else null. */
    public QuiclimeSession(ChannelHandler handler, EventLoopGroup group, String worldKey) {
        this.handler = handler;
        this.group = group;
        this.worldKey = worldKey;
    }

    public void startAsync() {
        var thread = new Thread(this::start, "e4mc_minecraft-init");
        thread.setDaemon(true);
        thread.start();
    }

    private static void addMessage(Component message) {
        Minecraft.getInstance().execute(() -> Minecraft.getInstance().gui.hud.getChat().addClientSystemMessage(message));
    }

    public static String[] getRelayMap() throws Exception {
        var httpClient = HttpClient.newHttpClient();
        var request = HttpRequest
                .newBuilder(new URI(Config.INSTANCE.dialtoneRelayMap.value()))
                .header("Accept", "application/json")
                .build();
        LOGGER.info("relaymap req: {}", request);
        var response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        LOGGER.info("relaymap resp: {}", response);
        if (response.statusCode() != 200) {
            throw new RuntimeException();
        }
        return gson.fromJson(response.body(), String[].class);
    }

    public void start() {
        try {
            String relayHost = Config.INSTANCE.relayHost.value();
            int relayPort = Config.INSTANCE.relayPort.value();
            LOGGER.info("using relay {}:{}", relayHost, relayPort);
            QuicSslContext context = QuicSslContextBuilder
                    .forClient()
                    .applicationProtocols("quiclime")
                    .build();
            var codec = new QuicClientCodecBuilder()
                    .sslContext(context)
                    .sslEngineProvider(it -> context.newEngine(it.alloc(), relayHost, relayPort))
                    .initialMaxStreamsBidirectional(512)
                    .maxIdleTimeout(10, TimeUnit.SECONDS)
                    .initialMaxData(4611686018427387903L)
                    .initialMaxStreamDataBidirectionalRemote(1250000)
                    .initialMaxStreamDataBidirectionalLocal(1250000)
                    .initialMaxStreamDataUnidirectional(1250000)
                    .build();
            Class<? extends DatagramChannel> channelClass = null;
            if (group instanceof EpollEventLoopGroup) {
                channelClass = EpollDatagramChannel.class;
            } else if (group instanceof NioEventLoopGroup) {
                channelClass = NioDatagramChannel.class;
            } else if (group instanceof KQueueEventLoopGroup) {
                channelClass = KQueueDatagramChannel.class;
            } else if (group instanceof MultiThreadIoEventLoopGroup mig) {
                if (mig.isIoType(EpollIoHandler.class)) {
                    channelClass = EpollDatagramChannel.class;
                } else if (mig.isIoType(NioIoHandler.class)) {
                    channelClass = NioDatagramChannel.class;
                } else if (mig.isIoType(KQueueIoHandler.class)) {
                    channelClass = KQueueDatagramChannel.class;
                }
            } else {
                throw new RuntimeException("Unknown EventLoopGroup " + group.getClass().getName());
            }
            new Bootstrap()
                    .group(group)
                    .channel(channelClass)
                    .handler(codec)
                    .bind(0)
                    .addListener(datagramChannelFuture -> {
                if (!datagramChannelFuture.isSuccess()) {
                    fail(datagramChannelFuture.cause());
                    throw new RuntimeException(datagramChannelFuture.cause());
                }
                datagramChannel = (DatagramChannel) ((ChannelFuture) datagramChannelFuture).channel();
                QuicChannel.newBootstrap(datagramChannel)
                        .streamHandler(handler)
                        .handler(new ChannelInboundHandlerAdapter() {
                            @Override
                            public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) throws Exception {
                                super.exceptionCaught(ctx, cause);
                                fail(cause);
                            }

                            @Override
                            public void channelInactive(ChannelHandlerContext ctx) throws Exception {
                                super.channelInactive(ctx);
                                state = State.STOPPED;
                            }
                        })
                        .remoteAddress(new InetSocketAddress(InetAddress.getByName(relayHost), relayPort))
                        .connect()
                        .addListener(quicChannelFuture -> {
                    if (!quicChannelFuture.isSuccess()) {
                        fail(quicChannelFuture.cause());
                        throw new RuntimeException(quicChannelFuture.cause());
                    }
                    quicChannel = (QuicChannel) quicChannelFuture.get();
                    quicChannel.createStream(QuicStreamType.BIDIRECTIONAL,
                            new ChannelInitializer<QuicStreamChannel>() {
                                @Override
                                protected void initChannel(QuicStreamChannel ch) {
                            ch.pipeline().addLast(new ControlMessageCodec(), new SimpleChannelInboundHandler<ControlMessageCodec.ControlMessage>() {
                                @Override
                                protected void channelRead0(ChannelHandlerContext ctx, ControlMessageCodec.ControlMessage msg) {
                                    if (msg instanceof ControlMessageCodec.HandedOffMessageClientbound) {
                                        handedOff.complete(null);
                                    } else if (msg instanceof ControlMessageCodec.DomainAssignmentFailedMessageClientbound failed) {
                                        LOGGER.error("Relay refused this world's address: {}", failed.reason);
                                        if (Agnos.isClient()) {
                                            addMessage(Component.translatable("name_in_use".equals(failed.reason)
                                                    ? "text.mcpersist.addressInUse"
                                                    : "text.mcpersist.addressRefused"));
                                        }
                                        state = State.UNHEALTHY;
                                    } else if (msg instanceof ControlMessageCodec.DomainAssignmentCompleteMessageClientbound) {
                                        state = State.STARTED;
                                        if (!Agnos.isClient()) {
                                            LOGGER.warn("e4mc running on Dedicated Server; This works, but isn't recommended as e4mc is designed for short-lived LAN servers");
                                        }
                                        String domain = ((ControlMessageCodec.DomainAssignmentCompleteMessageClientbound) msg).domain;
                                        QuiclimeSession.this.domain = domain;
                                        LOGGER.info("Domain assigned: {}", domain);
                                        if (Agnos.isClient()) {
                                            MutableComponent domainComponent = Config.INSTANCE.hideDomainInChat.value()
                                                    ? Component.translatable("text.e4mc_minecraft.hiddenDomain")
                                                    : Component.literal(domain);
                                            Component message = Component.translatable("text.e4mc_minecraft.domainAssigned",
                                                    domainComponent.withStyle(it -> it
                                                            .withClickEvent(new ClickEvent.CopyToClipboard(domain))
                                                            .withColor(ChatFormatting.GREEN)
                                                            .withHoverEvent(new HoverEvent.ShowText(Component.translatable("chat.copy.click")))))
                                                    .append(Component.translatable("text.e4mc_minecraft.clickToStop").withStyle(it -> it
                                                            .withClickEvent(new ClickEvent.RunCommand("/e4mc stop"))
                                                            .withColor(ChatFormatting.GRAY)));
                                            addMessage(message);
                                            // Whitelisting is only applied when the dedicated commands are.
                                            if (Config.INSTANCE.useWhiteList.value() && Config.INSTANCE.restoreDedicatedCommands.value()) {
                                                addMessage(Component.translatable("text.mcpersist.whitelistOn",
                                                        Component.literal("/whitelist add <name>").withStyle(it -> it
                                                                .withClickEvent(new ClickEvent.SuggestCommand("/whitelist add "))
                                                                .withColor(ChatFormatting.YELLOW))));
                                            }
                                            if (E4mcClient.badurl) {
                                                addMessage(Component.translatable("text.e4mc_minecraft.poisonpill.badurl"));
                                            }
                                        }
                                    }
                                    if (msg instanceof ControlMessageCodec.RequestMessageBroadcastMessageClientbound) {
                                        if (Agnos.isClient()) {
                                            addMessage(Component.literal(((ControlMessageCodec.RequestMessageBroadcastMessageClientbound) msg).message));
                                        }
                                    }
                                    if (msg instanceof ControlMessageCodec.HasCapabilitiesMessageClientbound) {
                                        var streamChannel = ctx.channel();
                                        boolean hasDialtoneSidecar = false;
                                        for (String cap : ((ControlMessageCodec.HasCapabilitiesMessageClientbound) msg).caps) {
                                            if (cap.equals("dialtone_sidecar")) {
                                                hasDialtoneSidecar = true;
                                                break;
                                            }
                                        }
                                        if (hasDialtoneSidecar && Config.INSTANCE.dialtoneHostEnabled.value()) {
                                            new ServerBootstrap()
                                                    .channel(DialtoneServerChannel.class)
                                                    .handler(new ChannelInboundHandlerAdapter() {
                                                        @Override
                                                        public void userEventTriggered(ChannelHandlerContext ctx, Object evt) throws Exception {
                                                            super.userEventTriggered(ctx, evt);
                                                            if (evt instanceof DialtoneAddress addr) {
                                                                var ticket = addr.actualAddress;
                                                                if (Config.INSTANCE.dialtoneSanitizeTicket.value()) {
                                                                    ticket = Endpoint.sanitizeTicket(ticket);
                                                                }
                                                                streamChannel
                                                                        .writeAndFlush(new ControlMessageCodec.DialtoneRegisterTicketMessageServerbound("v1_" + ticket))
                                                                        .addListener(ignored -> LOGGER.info("notified server of our ticket"));
                                                            }
                                                        }
                                                    })
                                                    .childHandler(handler)
                                                    .group(group)
                                                    .localAddress(new DialtoneAddress(""))
                                                    .bind()
                                                    .addListener(dialtoneChannelFuture -> {
                                                        if (!dialtoneChannelFuture.isSuccess()) {
                                                            fail(dialtoneChannelFuture.cause());
                                                            throw new RuntimeException(dialtoneChannelFuture.cause());
                                                        }
                                                        dialtoneChannel = (DialtoneServerChannel) dialtoneChannelFuture.get();
                                                    });
                                        }
                                    }
                                }
                            });
                        }
                    }).addListener(it -> {
                        if (!it.isSuccess()) {
                            fail(it.cause());
                            throw new RuntimeException(it.cause());
                        }
                        QuicStreamChannel streamChannel = (QuicStreamChannel) it.getNow();
                        controlChannel = streamChannel;
                        LOGGER.info("control channel open: {}", streamChannel);
                        streamChannel
                                .writeAndFlush(new ControlMessageCodec.ProbeCapabilitiesMessageServerbound())
                                .addListener(ignored -> LOGGER.info("probing capabilities"));
                        streamChannel
                                .writeAndFlush(new ControlMessageCodec.RequestDomainAssignmentMessageServerbound(worldKey))
                                .addListener(ignored -> LOGGER.info("control channel write complete"));
                        quicChannel.closeFuture().addListener(ignored -> datagramChannel.close());
                    });
                });
            });
        } catch (Throwable e) {
            fail(e);
            throw new RuntimeException(e);
        }
    }

    private void fail(Throwable e) {
        QuiclimeSession.this.state = State.UNHEALTHY;
        failureCause = e;
        E4mcClient.LOGGER.error("error in e4mc", e);
        if (Agnos.isClient()) {
            addMessage(Component.translatable("text.e4mc_minecraft.error"));
        }
    }

    private static void afterCloseIfPresent(Channel channel, Consumer<Boolean> callback) {
        if (channel == null) {
            callback.accept(false);
        } else {
            channel.close().addListener(it -> callback.accept(true));
        }
    }

    /**
     * Asks the relay to send new players to the world's next host (its background server)
     * instead of here, keeping current players connected. Whether the relay confirmed in time.
     */
    public boolean handOff(long timeout, TimeUnit unit) {
        QuicStreamChannel channel = controlChannel;
        if (channel == null || !channel.isActive()) {
            return false;
        }
        channel.writeAndFlush(new ControlMessageCodec.HandingOffMessageServerbound());
        try {
            handedOff.get(timeout, unit);
            return true;
        } catch (TimeoutException | ExecutionException e) {
            return false;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    public void stop() {
        state = State.STOPPING;
        afterCloseIfPresent(dialtoneChannel, q -> afterCloseIfPresent(quicChannel, a -> afterCloseIfPresent(datagramChannel, b -> state = State.STOPPED)));
    }


    private static ByteBuf writeVarInt(ByteBuf buf, int value) {
        while ((value & 0xffffff80) != 0) {
            buf.writeByte(value & 0x7F | 0x80);
            value >>>= 7;
        }

        buf.writeByte(value);
        return buf;
    }
}
