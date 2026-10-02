package link.mcpersist.mixin;

import link.mcpersist.Config;
import link.mcpersist.MCPersist;
import link.mcpersist.SessionPlayers;
import link.mcpersist.WorldPersistence;
import net.minecraft.network.Connection;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.CommonListenerCookie;
import net.minecraft.server.players.NameAndId;
import net.minecraft.server.players.PlayerList;
import net.minecraft.server.players.UserBanList;
import net.minecraft.server.players.UserWhiteList;
import net.minecraft.world.level.storage.LevelResource;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import java.io.IOException;
import java.net.SocketAddress;

@Mixin(PlayerList.class)
public abstract class PlayerListMixin {
    @Unique
    private static boolean mcpersist$hinted;

    @Shadow public abstract UserBanList getBans();

    @Shadow public abstract UserWhiteList getWhiteList();

    @Shadow public abstract MinecraftServer getServer();

    @Inject(method = "<init>", at = @At("TAIL"))
    void injectListLoads(CallbackInfo ci) {
        if (Config.restoreDedicatedCommands) {
            getServer().setUsingWhitelist(Config.useWhiteList);
            try {
                this.getBans().load();
            } catch (IOException e) {
                MCPersist.LOGGER.warn("Failed to load user banlist: ", e);
            }
            try {
                this.getWhiteList().load();
            } catch (IOException e) {
                MCPersist.LOGGER.warn("Failed to load whitelist: ", e);
            }
        }
    }

    /** Everyone who plays during the host's session is whitelisted on the background server. */
    @Inject(method = "placeNewPlayer", at = @At("TAIL"))
    void mcpersist$recordJoin(Connection connection, ServerPlayer player, CommonListenerCookie cookie, CallbackInfo ci) {
        ((SessionPlayers) getServer()).mcpersist$sessionPlayers().add(player.nameAndId());
        // Sharing hides in World Options, so tell the host where it is: once a launch, in a world
        // that isn't shared yet.
        MinecraftServer server = getServer();
        if (!mcpersist$hinted && Config.hostEnabled && !server.isDedicatedServer()
                && server.isSingleplayerOwner(player.nameAndId()) && !server.isPublished()
                && !WorldPersistence.isPersistent(server.getWorldPath(LevelResource.ROOT))) {
            mcpersist$hinted = true;
            player.sendSystemMessage(Component.translatable("text.mcpersist.howToShare"));
        }
    }

    @Inject(method = "canPlayerLogin", at = @At("HEAD"), cancellable = true)
    public void allowOwnerLogin(SocketAddress socketAddress, NameAndId player, CallbackInfoReturnable<Component> cir) {
        if (getServer().isSingleplayerOwner(player)) {
            cir.setReturnValue(null);
        }
    }
}
