package link.e4mc;

import net.fabricmc.api.EnvType;
import net.fabricmc.loader.api.FabricLoader;

import java.nio.file.Path;

public class Agnos {
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

    public static Path jarPath() {
        return FabricLoader.getInstance().getModContainer(E4mcClient.MOD_ID).get().getOrigin().getPaths().get(0);
    }
}
