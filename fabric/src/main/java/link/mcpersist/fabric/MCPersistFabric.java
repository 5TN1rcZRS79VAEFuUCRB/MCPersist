package link.mcpersist.fabric;

import link.mcpersist.MCPersist;
import net.fabricmc.api.ModInitializer;

public class MCPersistFabric implements ModInitializer {
    @Override
    public void onInitialize() {
        MCPersist.init();
        net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback.EVENT.register((dispatcher, ignored, ignored2) -> MCPersist.registerCommands(dispatcher));
    }
}
