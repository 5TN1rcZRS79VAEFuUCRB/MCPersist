package link.e4mc.mixin;

import link.e4mc.E4mcClient;
import link.e4mc.QuiclimeSession;
import link.e4mc.WorldPersistence;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.common.ClientboundTransferPacket;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.storage.LevelResource;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * When the host leaves a persistent world, sends everyone else to its stable address, where
 * the background server will pick them up (the relay holds them while it starts).
 */
@Mixin(IntegratedServer.class)
public abstract class IntegratedServerMixin {
    @Unique
    private boolean mcpersist$playersMoved;

    // halt() first drops every player but the host without a word, before the server stops,
    // so this has to run ahead of it. It can run more than once.
    @Inject(method = "halt", at = @At("HEAD"))
    private void mcpersist$transferPlayers(boolean waitForServer, CallbackInfo ci) {
        IntegratedServer server = (IntegratedServer) (Object) this;
        if (mcpersist$playersMoved || !WorldPersistence.isPersistent(server.getWorldPath(LevelResource.ROOT))) {
            return;
        }
        mcpersist$playersMoved = true;
        List<CompletableFuture<Void>> transfers = new ArrayList<>();
        server.executeBlocking(() -> {
            QuiclimeSession session = E4mcClient.session;
            // Until the relay stops routing new players here, a transferred player would
            // reconnect straight back to this closing game, which refuses transfers.
            String domain = session != null && session.domain != null && session.handOff(2, TimeUnit.SECONDS)
                    ? session.domain : null;
            for (ServerPlayer player : server.getPlayerList().getPlayers()) {
                if (server.isSingleplayerOwner(player.nameAndId())) {
                    continue;
                }
                E4mcClient.LOGGER.info("Moving {} to the background server at {}", player.nameAndId().name(), domain);
                if (domain != null) {
                    CompletableFuture<Void> written = new CompletableFuture<>();
                    player.connection.send(new ClientboundTransferPacket(domain, 25565), f -> written.complete(null));
                    transfers.add(written);
                } else {
                    player.connection.disconnect(Component.translatable("text.mcpersist.movingToBackground"));
                }
            }
        });
        if (!transfers.isEmpty()) {
            mcpersist$awaitDelivery(transfers);
        }
    }

    /**
     * Once the server stops, closing the relay's QUIC connection discards whatever it hasn't
     * delivered yet, so the transfers would never arrive.
     */
    @Unique
    private static void mcpersist$awaitDelivery(List<CompletableFuture<Void>> transfers) {
        try {
            CompletableFuture.allOf(transfers.toArray(CompletableFuture[]::new)).get(2, TimeUnit.SECONDS);
            // ponytail: fixed grace for the packets to cross the relay; wait for the players to
            // disconnect instead once relayed disconnects are noticed (#18).
            Thread.sleep(1000);
        } catch (TimeoutException | ExecutionException e) {
            E4mcClient.LOGGER.warn("Transfers to the background server may not have been sent", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
