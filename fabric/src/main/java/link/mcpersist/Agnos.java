package link.mcpersist;

import net.fabricmc.api.EnvType;
import net.fabricmc.loader.api.FabricLoader;

import java.nio.file.Path;

public class Agnos {
    public static final String LOADER = "fabric";

    public static boolean isClient() {
        return FabricLoader.getInstance().getEnvironmentType().equals(EnvType.CLIENT);
    }

    public static Path configDir() {
        return FabricLoader.getInstance().getConfigDir();
    }

    public static Path gameDir() {
        return FabricLoader.getInstance().getGameDir();
    }

    /** The version of a loaded mod, e.g. "minecraft" or "fabricloader". */
    public static String modVersion(String modId) {
        return FabricLoader.getInstance().getModContainer(modId).orElseThrow().getMetadata().getVersion().getFriendlyString();
    }

    /** The version of the loader itself, as its library jars name it. */
    public static String loaderVersion() {
        return modVersion("fabricloader");
    }

    /** NeoForge's --fml.neoFormVersion, which its server needs too; null on other loaders. */
    public static String neoFormVersion() {
        return null;
    }

    public static Path jarPath() {
        return FabricLoader.getInstance().getModContainer(MCPersist.MOD_ID).get().getOrigin().getPaths().get(0);
    }
}
