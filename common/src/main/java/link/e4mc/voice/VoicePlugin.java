package link.e4mc.voice;

import de.maxhenkel.voicechat.api.ForgeVoicechatPlugin;
import de.maxhenkel.voicechat.api.VoicechatPlugin;
import de.maxhenkel.voicechat.api.events.EventRegistration;
import de.maxhenkel.voicechat.api.events.VoiceHostEvent;
import de.maxhenkel.voicechat.api.events.VoicechatServerStartingEvent;
import io.netty.incubator.codec.quic.QuicStreamAddress;
import link.e4mc.E4mcClient;
import link.e4mc.QuiclimeSession;
import link.e4mc.dialtone.DialtoneAddress;
import net.minecraft.server.level.ServerPlayer;

import java.net.SocketAddress;

/**
 * Carries Simple Voice Chat through the relay for players who joined through it. Only that
 * mod loads this class (by its annotation on Forge and NeoForge, its "voicechat" entrypoint
 * on Fabric), so MCPersist doesn't need it installed.
 */
@ForgeVoicechatPlugin
public class VoicePlugin implements VoicechatPlugin {
    @Override
    public String getPluginId() {
        return E4mcClient.MOD_ID;
    }

    @Override
    public void registerEvents(EventRegistration registration) {
        registration.registerEvent(VoicechatServerStartingEvent.class,
                event -> event.setSocketImplementation(new RelayVoiceSocket()));
        registration.registerEvent(VoiceHostEvent.class, VoicePlugin::relayVoiceHost);
    }

    /**
     * A player who joined through the relay, or peer-to-peer over Dialtone after looking the
     * world up there, sends voice to the relay at the address they joined.
     */
    private static void relayVoiceHost(VoiceHostEvent event) {
        QuiclimeSession session = E4mcClient.session;
        Integer port = session == null ? null : session.voicePort;
        if (port == null || !(event.getPlayer().getPlayer() instanceof ServerPlayer player)) {
            return;
        }
        SocketAddress from = player.connection.getRemoteAddress();
        if (!(from instanceof QuicStreamAddress || from instanceof DialtoneAddress)) {
            return;
        }
        session.registerVoicePlayer(player.getUUID());
        // Just a port: the player's game uses it with the address they typed to join.
        event.setVoiceHost(String.valueOf(port));
    }
}
