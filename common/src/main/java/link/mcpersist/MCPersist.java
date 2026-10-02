package link.mcpersist;

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

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

public class MCPersist {
    public static final String MOD_ID = "mcpersist";
    public static volatile QuiclimeSession session;
    public static final Logger LOGGER = LoggerFactory.getLogger(MCPersist.MOD_ID);

    public static void init() {
        Config.load();
        // QUIC (for the relay) and iroh-java (for peer-to-peer) load the native library named by
        // their native_path property instead of downloading one, so the jar carries them (see
        // build.gradle). The suffixes are their native builds: when either library is updated,
        // update its suffix here and its files in build.gradle.
        bundledNative("link.e4mc.native_path", "netty_quiche", "74");
        bundledNative("link.e4mc.dialtone.native_path", "iroh_java", "57caf9a");
    }

    /**
     * Unpacks this platform's build of the library into the game folder and points the library at
     * it. The path is set even when the jar has no build for this platform, so the library fails
     * to load rather than downloading one.
     */
    private static void bundledNative(String pathProperty, String library, String build) {
        if (System.getProperty(pathProperty) != null) {
            return;
        }
        String file = System.mapLibraryName(library + "_" + PlatformDependent.normalizedOs() + "_"
                + PlatformDependent.normalizedArch() + "_" + build);
        Path dest = Agnos.gameDir().resolve("mcpersist").resolve("natives").resolve(file);
        System.setProperty(pathProperty, dest.toString());
        if (Files.exists(dest)) {
            return;
        }
        try (InputStream in = MCPersist.class.getResourceAsStream("/mcpersist-natives/" + file)) {
            if (in == null) {
                LOGGER.error("MCPersist has no {} for this platform", file);
                return;
            }
            Files.createDirectories(dest.getParent());
            // Copied whole, then moved into place, so a half-written file is never loaded.
            Path temp = Files.createTempFile(dest.getParent(), file, ".tmp");
            Files.copy(in, temp, StandardCopyOption.REPLACE_EXISTING);
            Files.move(temp, dest, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            LOGGER.error("couldn't unpack {}", file, e);
        }
    }

    public static void registerCommands(CommandDispatcher<CommandSourceStack> dispatcher) {
        if (Config.restoreDedicatedCommands && Agnos.isClient()) {
            BanListCommands.register(dispatcher);
            BanPlayerCommands.register(dispatcher);
            PardonCommand.register(dispatcher);
            WhitelistCommand.register(dispatcher);
        }
        dispatcher.register(
                Commands.literal("mcpersist")
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
                            if ((session != null) && !session.stopRequested()) {
                                session.stop();
                                ((SessionPlayers) ctx.getSource().getServer()).mcpersist$markUnshared();
                                ctx.getSource().sendSuccess(() -> Component.translatable("text.mcpersist.closeServer"), true);
                            } else {
                                ctx.getSource().sendFailure(Component.translatable("text.mcpersist.serverAlreadyClosed"));
                            }
                            return 1;
                        }))
                        .then(Commands.literal("doctor").executes(ctx -> {
                            var thread = new Thread(() -> {
                                LOGGER.info("generating MCPersist doctor report");
                                ctx.getSource().sendSuccess(() -> Component.translatable("text.mcpersist.doctor.start"), true);
                                var diag = Doctor.doctor();
                                LOGGER.info("MCPersist doctor report:\n{}", diag);
                                ctx.getSource().sendSuccess(() -> Component.literal(diag), true);
                            }, "mcpersist-doctor");
                            thread.setDaemon(true);
                            thread.start();
                            return 1;
                        }))
                        .then(Commands.literal("restart").executes(ctx -> {
                            if ((session != null) && (session.state != QuiclimeSession.State.STARTED)) {
                                session.stop();
                                session = new QuiclimeSession(session.handler, session.group, session.worldDir);
                                session.startAsync();
                                ((SessionPlayers) ctx.getSource().getServer()).mcpersist$markShared();
                            }
                            return 1;
                        }))
        );
    }
}
