package link.e4mc.mixin;

import net.minecraft.client.gui.screens.worldselection.WorldSelectionList;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

/** reloadWorldList is package-private on 1.20.1. */
@Mixin(WorldSelectionList.class)
public interface WorldSelectionListAccessor {
    @Invoker("reloadWorldList")
    void mcpersist$reloadWorldList();
}
