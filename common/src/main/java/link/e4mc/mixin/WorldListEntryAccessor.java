package link.e4mc.mixin;

import net.minecraft.client.gui.screens.worldselection.WorldSelectionList;
import net.minecraft.world.level.storage.LevelSummary;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** On 1.20.1 the world list's button update isn't told which world is selected. */
@Mixin(WorldSelectionList.WorldListEntry.class)
public interface WorldListEntryAccessor {
    @Accessor("summary")
    LevelSummary mcpersist$summary();
}
