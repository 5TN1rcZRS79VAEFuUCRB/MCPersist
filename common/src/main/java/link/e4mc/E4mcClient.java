package link.e4mc;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.exceptions.CommandSyntaxException;
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

    public static boolean badurl = false;

    public static void init() {
        Config.INSTANCE.id(); // Touch to initialize for McQoy
        try {
            if (!PoisonPill.checkMotw()) {
                badurl = true;
                LOGGER.warn("MotW lists unknown source! Poison pill active!");
            }
        } catch (Exception e) {
            LOGGER.warn("MotW check failed!", e);
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
                                session = new QuiclimeSession(session.handler, session.group, session.worldKey);
                                session.startAsync();
                            }
                            return 1;
                        }))
        );
    }
}
