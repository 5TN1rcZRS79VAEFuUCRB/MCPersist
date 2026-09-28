package link.e4mc;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.exceptions.CommandSyntaxException;
import io.netty.util.internal.PlatformDependent;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.commands.*;
import net.minecraft.server.permissions.Permissions;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class E4mcClient {
    public static final String MOD_ID = "mcpersist";
    public static QuiclimeSession session;
    public static final Logger LOGGER = LoggerFactory.getLogger(E4mcClient.MOD_ID);

    public static void init() {
        Config.INSTANCE.id(); // Loads the config, writing the file if it's missing
        // QUIC (for the relay) and iroh-java (for peer-to-peer) download their native libraries
        // on first use, from e4mc's CDN unless told otherwise; ours mirrors them. Each checks the
        // file's SHA-256 either way. The suffixes are their native builds: when either library
        // is updated, update its suffix here and the files on the mirror.
        mirrorNative("link.e4mc.native_url", "netty_quiche", "74");
        mirrorNative("link.e4mc.dialtone.native_url", "iroh_java", "57caf9a");
    }

    private static void mirrorNative(String urlProperty, String library, String build) {
        if (System.getProperty(urlProperty) == null) {
            String file = System.mapLibraryName(library + "_" + PlatformDependent.normalizedOs() + "_"
                    + PlatformDependent.normalizedArch() + "_" + build);
            System.setProperty(urlProperty, "https://mcpersist.com/natives/" + file);
        }
    }

    public static void registerCommands(CommandDispatcher<CommandSourceStack> dispatcher) {
        if (Config.INSTANCE.restoreDedicatedCommands.value() && Agnos.isClient()) {
            BanListCommands.register(dispatcher);
            BanPlayerCommands.register(dispatcher);
            PardonCommand.register(dispatcher);
            WhitelistCommand.register(dispatcher);
        }
        dispatcher.register(
                Commands.literal("e4mc")
                        .requires(src -> {
                            if (src.getServer() == null) {
                                return false;
                            }
                            if (src.getServer().isDedicatedServer()) {
                                return src.permissions().hasPermission(Permissions.COMMANDS_OWNER);
                            } else {
                                try {
                                    return src.getServer().isSingleplayerOwner(src.getPlayerOrException().nameAndId());
                                } catch (CommandSyntaxException e) {
                                    return false;
                                }
                            }
                        })
                        .then(Commands.literal("stop").executes(ctx -> {
                            if ((session != null) && (session.state != QuiclimeSession.State.STOPPED)) {
                                session.stop();
                                ctx.getSource().sendSuccess(() -> Component.translatable("text.e4mc_minecraft.closeServer"), true);
                            } else {
                                ctx.getSource().sendFailure(Component.translatable("text.e4mc_minecraft.serverAlreadyClosed"));
                            }
                            return 1;
                        }))
                        .then(Commands.literal("doctor").executes(ctx -> {
                            var thread = new Thread(() -> {
                                LOGGER.info("generating e4mc doctor report");
                                ctx.getSource().sendSuccess(() -> Component.translatable("text.e4mc_minecraft.doctor.start"), true);
                                var diag = Doctor.doctor();
                                LOGGER.info("e4mc doctor report:\n{}", diag);
                                ctx.getSource().sendSuccess(() -> Component.literal(diag), true);
                            }, "e4mc_minecraft-doctor");
                            thread.setDaemon(true);
                            thread.start();
                            return 1;
                        }))
                        .then(Commands.literal("restart").executes(ctx -> {
                            if ((session != null) && (session.state != QuiclimeSession.State.STARTED)) {
                                session.stop();
                                session = new QuiclimeSession(session.handler, session.group, session.worldDir);
                                session.startAsync();
                            }
                            return 1;
                        }))
        );
    }
}
