package link.mcpersist;

import net.minecraft.ChatFormatting;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.login.custom.CustomQueryAnswerPayload;
import net.minecraft.network.protocol.login.custom.CustomQueryPayload;
import net.minecraft.resources.Identifier;

import java.net.URI;

/**
 * The login query asking whether a player's client has MCPersist: a client with the mod answers
 * it, and one without answers with nothing, as it does any query it doesn't know.
 */
public final class ModCheck {
    public static final Identifier ID = Identifier.fromNamespaceAndPath(MCPersist.MOD_ID, "hello");
    // Clear of the small counters loaders number their own login queries with.
    public static final int TRANSACTION = 0x6D637073;
    // Literal, since a client without the mod has no translation for it.
    public static final Component REFUSED = Component.literal(
            "This server requires the MCPersist mod.\nGet it at modrinth.com/mod/mcpersist");

    // Shown to a player who joined through the relay without the mod.
    public static final Component TIP = Component.literal("This world is shared with MCPersist. With the mod, "
                    + "you'd connect straight to the host, peer-to-peer, instead of through the relay: usually "
                    + "faster and more stable. Get it at ")
            .append(Component.literal("modrinth.com/mod/mcpersist").withStyle(it -> it
                    .withClickEvent(new ClickEvent.OpenUrl(URI.create("https://modrinth.com/mod/mcpersist")))
                    .withColor(ChatFormatting.GREEN)))
            .withStyle(ChatFormatting.GRAY);

    public static final CustomQueryPayload QUERY = new CustomQueryPayload() {
        @Override
        public Identifier id() {
            return ID;
        }

        @Override
        public void write(FriendlyByteBuf buf) {
        }
    };

    public static final CustomQueryAnswerPayload ANSWER = buf -> {};

    private ModCheck() {}
}
