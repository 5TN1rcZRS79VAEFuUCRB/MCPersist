package link.mcpersist.neoforge;

import link.mcpersist.MCPersist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.RegisterCommandsEvent;

@Mod(MCPersist.MOD_ID)
public class MCPersistNeoForge {
    public MCPersistNeoForge() {
        MCPersist.init();
        NeoForge.EVENT_BUS.register(this);
    }

    @SubscribeEvent
    public void onRegisterCommandEvent(RegisterCommandsEvent event) {
        MCPersist.registerCommands(event.getDispatcher());
    }
}
