package link.mcpersist.forge;

import link.mcpersist.MCPersist;
import net.minecraftforge.event.RegisterCommandsEvent;
import net.minecraftforge.fml.common.Mod;

@Mod(MCPersist.MOD_ID)
public class MCPersistForge {
    public MCPersistForge() {
        MCPersist.init();
        RegisterCommandsEvent.BUS.addListener(event -> MCPersist.registerCommands(event.getDispatcher()));
    }
}
