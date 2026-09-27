package link.e4mc.mixin;

import link.e4mc.E4mcClient;
import link.e4mc.QuiclimeSession;
import link.e4mc.WorldPersistence;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.storage.LevelResource;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * When the host leaves a persistent world, disconnects everyone else so they can rejoin its
 * stable address, where the background server will pick them up (the relay holds them while it
 * starts). 1.20.1 has no transfer packet to move them there directly.
 */
@Mixin(IntegratedServer.class)
public abstract class IntegratedServerMixin {
    /** Only touched on the server thread. */
    @Unique
    private boolean mcpersist$playersMoved;

    // halt() first drops every player but the host without a word, before the server stops,
    // so this has to run ahead of it. The game and the server thread (when the host's
    // connection closes) can both call halt(); whichever reaches the server thread first moves
    // the players, ahead of its own drop.
    @Inject(method = "halt", at = @At("HEAD"))
    private void mcpersist$movePlayers(boolean waitForServer, CallbackInfo ci) {
        IntegratedServer server = (IntegratedServer) (Object) this;
        if (!WorldPersistence.isPersistent(server.getWorldPath(LevelResource.ROOT))) {
            return;
        }
        AtomicBoolean anyDisconnected = new AtomicBoolean();
        server.executeBlocking(() -> {
            if (mcpersist$playersMoved) {
                return;
            }
            mcpersist$playersMoved = true;
            QuiclimeSession session = E4mcClient.session;
            // Until the relay stops routing new players here, a player who rejoins would
            // reconnect straight back to this closing game.
            if (session != null && session.domain != null) {
                session.handOff(2, TimeUnit.SECONDS);
            }
            Component reason = Component.translatable("text.mcpersist.movingToBackground");
            // A copy: disconnecting removes the player from the list right away on this thread.
            for (ServerPlayer player : List.copyOf(server.getPlayerList().getPlayers())) {
                if (server.isSingleplayerOwner(player.getGameProfile())) {
                    continue;
                }
                E4mcClient.LOGGER.info("Sending {} to the background server", player.getGameProfile().getName());
                player.connection.disconnect(reason);
                anyDisconnected.set(true);
            }
        });
        if (anyDisconnected.get()) {
            // Once the server stops, closing the relay's QUIC connection discards whatever it
            // hasn't delivered yet, so the players would never see why they were dropped.
            // ponytail: fixed grace for the packets to cross the relay; wait for the players to
            // disconnect instead once relayed disconnects are noticed (#18).
            try {
                Thread.sleep(1000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }
}
