package link.mcpersist.mixin;

import link.mcpersist.TicketSmuggler;
import net.minecraft.client.multiplayer.resolver.ServerAddress;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;

@Mixin(ServerAddress.class)
public class ServerAddressMixin implements TicketSmuggler {
    @Unique
    private String mcpersist$smuggledTicket = null;

    @Override
    public void mcpersist$setSmuggledTicket(String ticket) {
        mcpersist$smuggledTicket = ticket;
    }

    @Override
    public String mcpersist$getSmuggledTicket() {
        return mcpersist$smuggledTicket;
    }
}
