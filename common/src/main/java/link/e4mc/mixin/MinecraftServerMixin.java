package link.e4mc.mixin;

import link.e4mc.LocalHandoff;
import com.mojang.authlib.GameProfile;
import link.e4mc.E4mcClient;
import link.e4mc.QuiclimeSession;
import link.e4mc.SessionPlayers;
import link.e4mc.WorldPersistence;
import link.e4mc.handoff.Handoff;
import net.minecraft.CrashReport;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.common.ClientboundTransferPacket;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.players.NameAndId;
import net.minecraft.server.players.PlayerList;
import net.minecraft.world.level.storage.LevelResource;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/** When the host leaves a persistent world, hands it to a background server. */
@Mixin(MinecraftServer.class)
public abstract class MinecraftServerMixin implements SessionPlayers {
    @Unique
    private final Set<NameAndId> mcpersist$sessionPlayers = ConcurrentHashMap.newKeySet();

    @Shadow
    public abstract boolean isDedicatedServer();

    @Shadow
    public abstract GameProfile getSingleplayerProfile();

    @Shadow
    public abstract PlayerList getPlayerList();

    @Shadow
    public abstract boolean isSingleplayerOwner(NameAndId player);

    @Override
    public Set<NameAndId> mcpersist$sessionPlayers() {
        return mcpersist$sessionPlayers;
    }

    @Shadow
    public abstract Path getWorldPath(LevelResource resource);

    /**
     * Before the host's world stops, sends everyone else to its stable address, where the
     * background server will pick them up (the relay holds them while it starts).
     */
    @Inject(method = "stopServer", at = @At("HEAD"))
    private void mcpersist$transferPlayers(CallbackInfo ci) {
        if (isDedicatedServer() || !WorldPersistence.isPersistent(getWorldPath(LevelResource.ROOT))) {
            return;
        }
        QuiclimeSession session = E4mcClient.session;
        String domain = session != null ? session.domain : null;
        List<CompletableFuture<Void>> transfers = new ArrayList<>();
        for (ServerPlayer player : getPlayerList().getPlayers()) {
            if (isSingleplayerOwner(player.nameAndId())) {
                continue;
            }
            if (domain != null) {
                CompletableFuture<Void> written = new CompletableFuture<>();
                player.connection.send(new ClientboundTransferPacket(domain, 25565), f -> written.complete(null));
                transfers.add(written);
            } else {
                player.connection.disconnect(Component.translatable("text.mcpersist.movingToBackground"));
            }
        }
        if (!transfers.isEmpty()) {
            mcpersist$awaitDelivery(transfers);
        }
    }

    /**
     * Right after this, vanilla stops the network, and closing the relay's QUIC connection
     * discards whatever it hasn't delivered yet, so the transfers would never arrive.
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

    @Unique
    private boolean mcpersist$crashed;

    /** Marks the session open, so a session that dies without a handoff is noticed next launch. */
    @Inject(method = "runServer", at = @At("HEAD"))
    private void mcpersist$markSessionOpen(CallbackInfo ci) {
        Path worldDir = getWorldPath(LevelResource.ROOT).toAbsolutePath().normalize();
        if (isDedicatedServer() || !WorldPersistence.isPersistent(worldDir)) {
            return;
        }
        try {
            Handoff.markSessionOpen(LocalHandoff.serversDir(), worldDir);
        } catch (IOException e) {
            E4mcClient.LOGGER.error("Failed to mark {} open", worldDir, e);
        }
    }

    @Inject(method = "onServerCrash", at = @At("HEAD"))
    private void mcpersist$noteCrash(CrashReport report, CallbackInfo ci) {
        mcpersist$crashed = true;
    }

    // At the end of stopServer the world is saved and its storage (and session.lock) closed.
    // Runs synchronously: quitting or closing the game waits for the server to finish
    // stopping, so the handoff completes before the game exits.
    @Inject(method = "stopServer", at = @At("TAIL"))
    private void mcpersist$handOff(CallbackInfo ci) {
        if (isDedicatedServer()) {
            return;
        }
        Path worldDir = getWorldPath(LevelResource.ROOT).toAbsolutePath().normalize();
        // After a crash, don't hand the world off and keep the session marked open: the next
        // launch offers to start the background server.
        if (mcpersist$crashed) {
            return;
        }
        try {
            Handoff.clearSessionOpen(LocalHandoff.serversDir(), worldDir);
        } catch (IOException e) {
            E4mcClient.LOGGER.error("Failed to mark {} closed", worldDir, e);
        }
        GameProfile owner = getSingleplayerProfile();
        if (owner == null || !WorldPersistence.isPersistent(worldDir)) {
            return;
        }
        NameAndId host = new NameAndId(owner);
        LocalHandoff.start(worldDir, new Handoff.Player(host.id(), host.name()),
                mcpersist$sessionPlayers.stream().map(p -> new Handoff.Player(p.id(), p.name())).toList());
    }
}
