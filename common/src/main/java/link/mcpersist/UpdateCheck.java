package link.mcpersist;

import com.google.gson.JsonParser;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.function.Consumer;

/**
 * Tells the host when a newer MCPersist is out: one chat line when they start sharing a world.
 * It only reads the newest version number from mcpersist.com, which the relay keeps up to date
 * from GitHub; it never downloads anything.
 */
public final class UpdateCheck {
    private static final URI LATEST = URI.create("https://mcpersist.com/latest.json");
    private static volatile boolean checked;

    private UpdateCheck() {}

    /** Checks once per game launch, in the background; tells {@code chat} only if there's something newer. */
    static void check(Consumer<Component> chat) {
        if (!Config.checkForUpdates || checked) {
            return;
        }
        checked = true;
        String current = Agnos.modVersion(MCPersist.MOD_ID);
        HttpClient.newHttpClient()
                .sendAsync(HttpRequest.newBuilder(LATEST).timeout(Duration.ofSeconds(10)).build(), HttpResponse.BodyHandlers.ofString())
                .thenAccept(response -> {
                    if (response.statusCode() != 200) {
                        return;
                    }
                    String latest = JsonParser.parseString(response.body()).getAsJsonObject().get("version").getAsString();
                    if (newer(latest, current)) {
                        chat.accept(Component.translatable("text.mcpersist.updateAvailable", latest, current,
                                        Component.literal("mcpersist.com").withStyle(it -> it
                                                .withClickEvent(new ClickEvent.OpenUrl(URI.create("https://mcpersist.com")))
                                                .withColor(ChatFormatting.GREEN)))
                                .withStyle(ChatFormatting.GRAY));
                    }
                })
                .exceptionally(e -> {
                    MCPersist.LOGGER.debug("Couldn't check for a newer MCPersist", e);
                    return null;
                });
    }

    /**
     * Whether version a is newer than b, for versions like "2.0.0-alpha.27": the numbers before
     * the "-" first, then a release beats any pre-release, then the pre-release numbers.
     */
    static boolean newer(String a, String b) {
        String[] pa = a.split("-", 2), pb = b.split("-", 2);
        int core = compareNumbers(pa[0], pb[0]);
        if (core != 0) {
            return core > 0;
        }
        if (pa.length != pb.length) {
            return pa.length < pb.length;  // No pre-release part: the release, newer.
        }
        return pa.length == 2 && compareNumbers(pa[1], pb[1]) > 0;
    }

    private static int compareNumbers(String a, String b) {
        String[] na = a.replaceAll("[^0-9]+", " ").trim().split(" "), nb = b.replaceAll("[^0-9]+", " ").trim().split(" ");
        for (int i = 0; i < Math.max(na.length, nb.length); i++) {
            long x = i < na.length && !na[i].isEmpty() ? Long.parseLong(na[i]) : 0;
            long y = i < nb.length && !nb[i].isEmpty() ? Long.parseLong(nb[i]) : 0;
            if (x != y) {
                return Long.compare(x, y);
            }
        }
        return 0;
    }

    public static void main(String[] args) {
        // ponytail: assert self-check of the version comparison; run with java -ea.
        assert newer("2.0.0-alpha.28", "2.0.0-alpha.27");
        assert newer("2.0.0-alpha.10", "2.0.0-alpha.9");
        assert !newer("2.0.0-alpha.27", "2.0.0-alpha.27");
        assert !newer("2.0.0-alpha.26", "2.0.0-alpha.27");
        assert newer("2.0.0", "2.0.0-alpha.27");
        assert !newer("2.0.0-alpha.27", "2.0.0");
        assert newer("2.1.0-alpha.1", "2.0.0");
        System.out.println("ok");
    }
}
