package link.mcpersist;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import io.netty.bootstrap.Bootstrap;
import io.netty.bootstrap.ServerBootstrap;
import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import io.netty.channel.*;
import io.netty.channel.epoll.EpollDatagramChannel;
import io.netty.channel.epoll.EpollIoHandler;
import io.netty.channel.kqueue.KQueueDatagramChannel;
import io.netty.channel.kqueue.KQueueIoHandler;
import io.netty.channel.nio.NioIoHandler;
import io.netty.channel.socket.DatagramChannel;
import io.netty.channel.socket.nio.NioDatagramChannel;
import io.netty.handler.codec.MessageToMessageCodec;
import io.netty.incubator.codec.quic.*;
import link.mcpersist.dialtone.DialtoneAddress;
import link.mcpersist.dialtone.DialtoneServerChannel;
import link.e4mc.iroh.Endpoint;
import net.minecraft.ChatFormatting;
import net.minecraft.client.Minecraft;
import net.minecraft.network.Varint21FrameDecoder;
import net.minecraft.network.Varint21LengthFieldPrepender;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.HoverEvent;
import net.minecraft.network.chat.MutableComponent;
import link.mcpersist.voice.RelayVoice;

import java.net.Inet6Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

public class QuiclimeSession {
    private static final Gson gson = new Gson();
    final ChannelHandler handler;

    private static class ControlMessageCodec extends MessageToMessageCodec<ByteBuf, ControlMessageCodec.ControlMessage> {
        public interface ControlMessage {}

        public static class ProbeCapabilitiesMessageServerbound implements ControlMessage {
            String kind = "probe_capabilities";
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

        public static class VoiceRegisterPlayerMessageServerbound implements ControlMessage {
            String kind = "voice_register_player";
            String uuid;
            public VoiceRegisterPlayerMessageServerbound(UUID uuid) {
                this.uuid = uuid.toString();
            }
        }

        public static class HandedOffMessageClientbound implements ControlMessage {}

        public static class DomainAssignmentCompleteMessageClientbound implements ControlMessage {
            String domain;
        }

        public static class DomainAssignmentFailedMessageClientbound implements ControlMessage {
            String reason;
        }

        public static class RequestMessageBroadcastMessageClientbound implements ControlMessage {
            String message;
        }

        public static class HasCapabilitiesMessageClientbound implements ControlMessage {
            String[] caps;
            // The UDP port players send voice to, from relays that carry it.
            Integer voice_port;
        }

        /** The relay's messages this session acts on, by kind; it skips the rest. */
        private static final Map<String, Class<? extends ControlMessage>> CLIENTBOUND = Map.of(
                "domain_assignment_complete", DomainAssignmentCompleteMessageClientbound.class,
                "domain_assignment_failed", DomainAssignmentFailedMessageClientbound.class,
                "request_message_broadcast", RequestMessageBroadcastMessageClientbound.class,
                "has_capabilities", HasCapabilitiesMessageClientbound.class,
                "handed_off", HandedOffMessageClientbound.class);

        // One message per frame: Minecraft's own varint-length framing sits ahead of this codec.
        @Override
        protected void encode(ChannelHandlerContext ctx, ControlMessage msg, List<Object> out) {
            out.add(Unpooled.wrappedBuffer(gson.toJson(msg).getBytes(StandardCharsets.UTF_8)));
        }

        @Override
        protected void decode(ChannelHandlerContext ctx, ByteBuf in, List<Object> out) {
            var json = gson.fromJson(in.toString(StandardCharsets.UTF_8), JsonObject.class);
            var type = CLIENTBOUND.get(json.get("kind").getAsString());
            if (type != null) {
                out.add(gson.fromJson(json, type));
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
    final Path worldDir;
    /** The world's key if it's persistent, else null; read when the session starts. */
    private String worldKey;
    /** The relay this session hosts through, once chosen. */
    public volatile String relayHost;
    /** The address the relay assigned, once assigned. */
    public volatile String domain;
    /** The relay's port for players' Simple Voice Chat traffic, if it carries it. */
    public volatile Integer voicePort;
    private volatile QuicStreamChannel controlChannel;
    private final CompletableFuture<Void> handedOff = new CompletableFuture<>();

    public QuiclimeSession(ChannelHandler handler, EventLoopGroup group, Path worldDir) {
        this.handler = handler;
        this.group = group;
        this.worldDir = worldDir;
    }

    public void startAsync() {
        var thread = new Thread(this::start, "mcpersist-init");
        thread.setDaemon(true);
        thread.start();
    }

    private static void addMessage(Component message) {
        Minecraft.getInstance().execute(() -> Minecraft.getInstance().gui.hud.getChat().addClientSystemMessage(message));
    }

    public static String[] getRelayMap() throws Exception {
        var httpClient = HttpClient.newHttpClient();
        var request = HttpRequest
                // Our own iroh relays, so peer-to-peer connections don't depend on e4mc's servers.
                .newBuilder(new URI("https://mcpersist.com/relaymap.json"))
                .header("Accept", "application/json")
                .build();
        var response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw new RuntimeException();
        }
        return gson.fromJson(response.body(), String[].class);
    }

    public void start() {
        try {
            String relayHost = this.relayHost = Relays.forWorld(worldDir, Config.relay);
            worldKey = WorldPersistence.keyFor(worldDir);
            int relayPort = Config.relayPort;
            MCPersist.LOGGER.info("using relay {}:{}", relayHost, relayPort);
            InetAddress relay = InetAddress.getByName(relayHost);
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
                    // Simple Voice Chat traffic of players who joined through the relay.
                    .datagram(1024, 1024)
                    .build();
            // Netty 4.2's older group classes are MultiThreadIoEventLoopGroups too.
            if (!(group instanceof MultiThreadIoEventLoopGroup mig)) {
                throw new RuntimeException("Unknown EventLoopGroup " + group.getClass().getName());
            }
            Class<? extends DatagramChannel> channelClass = null;
            if (mig.isIoType(EpollIoHandler.class)) {
                channelClass = EpollDatagramChannel.class;
            } else if (mig.isIoType(NioIoHandler.class)) {
                channelClass = NioDatagramChannel.class;
            } else if (mig.isIoType(KQueueIoHandler.class)) {
                channelClass = KQueueDatagramChannel.class;
            }
            new Bootstrap()
                    .group(group)
                    .channel(channelClass)
                    .handler(codec)
                    // The relay's own family: Forge and NeoForge switch a server bound to an IPv4
                    // address to IPv4-only sockets, where the default IPv6 wildcard can't bind.
                    .bind(new InetSocketAddress(relay instanceof Inet6Address ? "::" : "0.0.0.0", 0))
                    .addListener(datagramChannelFuture -> {
                if (!datagramChannelFuture.isSuccess()) {
                    fail(datagramChannelFuture.cause());
                    throw new RuntimeException(datagramChannelFuture.cause());
                }
                datagramChannel = (DatagramChannel) ((ChannelFuture) datagramChannelFuture).channel();
                QuicChannel.newBootstrap(datagramChannel)
                        .streamHandler(new ChannelInitializer<QuicStreamChannel>() {
                            @Override
                            protected void initChannel(QuicStreamChannel ch) {
                                // The relay resets a player's stream when they leave. Handed to the
                                // game as an error, it answers with a disconnect packet and waits for
                                // it to be sent, which on a stream that's already gone can never
                                // happen: the player stayed until the 30 s read timeout. Closing the
                                // stream drops them at once.
                                ch.pipeline().addLast(new ChannelInboundHandlerAdapter() {
                                    @Override
                                    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
                                        if (cause instanceof QuicException) {
                                            ctx.close();
                                        } else {
                                            ctx.fireExceptionCaught(cause);
                                        }
                                    }
                                }, handler);
                            }
                        })
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

                            // QUIC datagrams: voice from players who joined through the relay.
                            @Override
                            public void channelRead(ChannelHandlerContext ctx, Object msg) {
                                if (msg instanceof ByteBuf datagram) {
                                    try {
                                        RelayVoice.received(datagram);
                                    } finally {
                                        datagram.release();
                                    }
                                } else {
                                    ctx.fireChannelRead(msg);
                                }
                            }
                        })
                        .remoteAddress(new InetSocketAddress(relay, relayPort))
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
                            ch.pipeline().addLast(new Varint21FrameDecoder(null), new Varint21LengthFieldPrepender(), new ControlMessageCodec(), new SimpleChannelInboundHandler<ControlMessageCodec.ControlMessage>() {
                                @Override
                                protected void channelRead0(ChannelHandlerContext ctx, ControlMessageCodec.ControlMessage msg) {
                                    if (msg instanceof ControlMessageCodec.HandedOffMessageClientbound) {
                                        handedOff.complete(null);
                                    } else if (msg instanceof ControlMessageCodec.DomainAssignmentFailedMessageClientbound failed) {
                                        MCPersist.LOGGER.error("Relay refused this world's address: {}", failed.reason);
                                        if (Agnos.isClient()) {
                                            addMessage(Component.translatable("name_in_use".equals(failed.reason)
                                                    ? "text.mcpersist.addressInUse"
                                                    : "text.mcpersist.addressRefused"));
                                        }
                                        state = State.UNHEALTHY;
                                    } else if (msg instanceof ControlMessageCodec.DomainAssignmentCompleteMessageClientbound complete) {
                                        state = State.STARTED;
                                        String domain = complete.domain;
                                        QuiclimeSession.this.domain = domain;
                                        MCPersist.LOGGER.info("Domain assigned: {}", domain);
                                        if (Agnos.isClient()) {
                                            MutableComponent domainComponent = Config.hideDomainInChat
                                                    ? Component.translatable("text.mcpersist.hiddenDomain")
                                                    : Component.literal(domain);
                                            Component message = Component.translatable("text.mcpersist.domainAssigned",
                                                    domainComponent.withStyle(it -> it
                                                            .withClickEvent(new ClickEvent.CopyToClipboard(domain))
                                                            .withColor(ChatFormatting.GREEN)
                                                            .withHoverEvent(new HoverEvent.ShowText(Component.translatable("chat.copy.click")))))
                                                    .append(Component.translatable("text.mcpersist.clickToStop").withStyle(it -> it
                                                            .withClickEvent(new ClickEvent.RunCommand("/mcpersist stop"))
                                                            .withColor(ChatFormatting.GRAY)));
                                            addMessage(message);
                                            if (LocalHandoff.usesWhitelist()) {
                                                addMessage(Component.translatable("text.mcpersist.whitelistOn",
                                                        Component.literal("/whitelist add <name>").withStyle(it -> it
                                                                .withClickEvent(new ClickEvent.SuggestCommand("/whitelist add "))
                                                                .withColor(ChatFormatting.YELLOW))));
                                            }
                                        }
                                    }
                                    if (msg instanceof ControlMessageCodec.RequestMessageBroadcastMessageClientbound broadcast) {
                                        MCPersist.LOGGER.info("Relay announcement: {}", broadcast.message);
                                        if (Agnos.isClient()) {
                                            addMessage(Component.literal(broadcast.message));
                                        }
                                    }
                                    if (msg instanceof ControlMessageCodec.HasCapabilitiesMessageClientbound caps) {
                                        if (Arrays.asList(caps.caps).contains("voice")) {
                                            voicePort = caps.voice_port;
                                            MCPersist.LOGGER.info("Relay carries voice chat on port {}", voicePort);
                                        }
                                        var streamChannel = ctx.channel();
                                        if (Arrays.asList(caps.caps).contains("dialtone_sidecar") && Config.dialtoneHostEnabled) {
                                            new ServerBootstrap()
                                                    .channel(DialtoneServerChannel.class)
                                                    .handler(new ChannelInboundHandlerAdapter() {
                                                        @Override
                                                        public void userEventTriggered(ChannelHandlerContext ctx, Object evt) throws Exception {
                                                            super.userEventTriggered(ctx, evt);
                                                            if (evt instanceof DialtoneAddress addr) {
                                                                // Without direct IP addresses, so the relay never sees them.
                                                                var ticket = Endpoint.sanitizeTicket(addr.actualAddress);
                                                                streamChannel
                                                                        .writeAndFlush(new ControlMessageCodec.DialtoneRegisterTicketMessageServerbound("v1_" + ticket));
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
                        MCPersist.LOGGER.info("control channel open: {}", streamChannel);
                        streamChannel
                                .writeAndFlush(new ControlMessageCodec.ProbeCapabilitiesMessageServerbound());
                        streamChannel
                                .writeAndFlush(new ControlMessageCodec.RequestDomainAssignmentMessageServerbound(worldKey));
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
        MCPersist.LOGGER.error("error in MCPersist", e);
        if (Agnos.isClient()) {
            addMessage(Component.translatable("text.mcpersist.error"));
        }
    }

    private static void afterCloseIfPresent(Channel channel, Runnable callback) {
        if (channel == null) {
            callback.run();
        } else {
            channel.close().addListener(it -> callback.run());
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

    /** Has the relay send this player's voice here. */
    public void registerVoicePlayer(UUID player) {
        QuicStreamChannel channel = controlChannel;
        if (channel != null && channel.isActive()) {
            channel.writeAndFlush(new ControlMessageCodec.VoiceRegisterPlayerMessageServerbound(player));
        }
    }

    /** Voice for a player who joined through the relay. */
    public void sendVoice(RelayVoice.Player player, byte[] packet) {
        QuicChannel channel = quicChannel;
        if (channel != null && channel.isActive()) {
            channel.writeAndFlush(RelayVoice.frame(player, packet));
        }
    }

    public void stop() {
        state = State.STOPPING;
        afterCloseIfPresent(dialtoneChannel, () -> afterCloseIfPresent(quicChannel, () -> afterCloseIfPresent(datagramChannel, () -> state = State.STOPPED)));
    }

}
