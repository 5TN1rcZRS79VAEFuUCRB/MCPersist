package link.mcpersist.dialtone;

import io.netty.channel.DefaultEventLoopGroup;
import io.netty.channel.EventLoopGroup;
import link.mcpersist.MCPersist;
import link.mcpersist.QuiclimeSession;
import link.e4mc.iroh.Endpoint;
import link.e4mc.iroh.NativeException;

import java.nio.charset.StandardCharsets;

public class DialtoneAmbientSession {
    public static final DialtoneAmbientSession INSTANCE = new DialtoneAmbientSession();

    public EventLoopGroup group = new DefaultEventLoopGroup();
    Endpoint endpoint;
    Thread dispatcher;

    private DialtoneAmbientSession() {}

    public void start() throws Exception {
        MCPersist.LOGGER.info("Starting DialtoneAmbientSession!");
        this.endpoint = newEndpoint();
        this.dispatcher = dispatch(endpoint, "Dialtone Session Dispatcher");
    }

    static Endpoint newEndpoint() throws Exception {
        return new Endpoint(new byte[][]{"e4mc-dialtone".getBytes(StandardCharsets.UTF_8)}, QuiclimeSession.getRelayMap());
    }

    /** Runs the endpoint's callbacks on a new daemon thread until the endpoint fails. */
    static Thread dispatch(Endpoint endpoint, String name) {
        Thread dispatcher = new Thread(() -> {
            while (true) {
                try {
                    Runnable polled = endpoint.pollCallbackLoop();
                    polled.run();
                } catch (NativeException e) {
                    MCPersist.LOGGER.error("poll exc, stopping", e);
                    throw e;
                } catch (Throwable e) {
                    MCPersist.LOGGER.error("poll exc, continuing", e);
                }
            }
        }, name);
        dispatcher.setDaemon(true);
        dispatcher.start();
        return dispatcher;
    }

    static void close(Endpoint endpoint, Thread dispatcher) {
        endpoint.closeAsync().join();
        endpoint.close();
        dispatcher.interrupt();
    }

    public void stop() {
        close(endpoint, dispatcher);
        endpoint = null;
        dispatcher = null;
    }
}
