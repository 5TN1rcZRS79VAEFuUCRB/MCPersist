package link.e4mc;

import net.neoforged.api.distmarker.Dist;
import net.neoforged.fml.ModList;
import net.neoforged.fml.loading.FMLEnvironment;
import net.neoforged.fml.loading.FMLPaths;

import java.nio.file.Path;

public class Agnos {
    public static final String LOADER = "neoforge";

    public static boolean isClient() {
        return FMLEnvironment.getDist() == Dist.CLIENT;
    }

    public static Path configDir() {
        return FMLPaths.CONFIGDIR.get();
    }

    public static Path gameDir() {
        return FMLPaths.GAMEDIR.get();
    }

    /** The version of a loaded mod, e.g. "minecraft" or "neoforge". */
    public static String modVersion(String modId) {
        return ModList.get().getModContainerById(modId).orElseThrow().getModInfo().getVersion().toString();
    }

    public static Path jarPath() {
        return ModList.get().getModFileById(E4mcClient.MOD_ID).getFile().getFilePath();
    }
}
