package link.e4mc;

import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.fml.ModList;
import net.minecraftforge.fml.loading.FMLLoader;
import net.minecraftforge.fml.loading.FMLPaths;

import java.nio.file.Path;

public class Agnos {
    public static final String LOADER = "forge";

    public static boolean isClient() {
        return FMLLoader.getDist() == Dist.CLIENT;
    }

    public static Path configDir() {
        return FMLPaths.CONFIGDIR.get();
    }

    public static Path gameDir() {
        return FMLPaths.GAMEDIR.get();
    }

    /** The version of a loaded mod, e.g. "minecraft" or "forge". */
    public static String modVersion(String modId) {
        return ModList.getModContainerById(modId).orElseThrow().getModInfo().getVersion().toString();
    }

    public static Path jarPath() {
        return ModList.getModFileById(E4mcClient.MOD_ID).getFile().getFilePath();
    }
}
