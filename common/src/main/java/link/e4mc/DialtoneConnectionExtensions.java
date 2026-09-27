package link.e4mc;

public interface DialtoneConnectionExtensions {
    byte[] e4mc$exportKeyingMaterial(byte[] label, byte[] context, int length);

    /** Whether this is a Dialtone connection; a joining player's reports the dialed address. */
    boolean e4mc$isDialtone();
}
