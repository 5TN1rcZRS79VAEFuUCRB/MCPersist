package link.mcpersist;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;

public class Doctor {
    public static String doctor() {
        var result = new StringBuilder();
        result.append("mod sha512sum: ");
        try {
            var bytes = Files.readAllBytes(Agnos.jarPath());
            var md = MessageDigest.getInstance("SHA-512");
            var digest = md.digest(bytes);
            result.append(HexFormat.of().formatHex(digest));
        } catch (Exception e) {
            result.append("exception during digest:\n");
            result.append(stackTrace(e));
        }
        result.append("\n");
        result.append("QuiclimeSession recorded exception:\n");
        var session = MCPersist.session;
        if (session != null && session.failureCause != null) {
            result.append(stackTrace(session.failureCause));
            result.append("\n");
        } else {
            result.append("none recorded.\n");
        }
        for (String property : new String[]{"link.e4mc.native_path", "link.e4mc.dialtone.native_path"}) {
            String path = System.getProperty(property);
            boolean present = path != null && Files.isRegularFile(Path.of(path));
            result.append(String.format("native %s: %s%s\n", property, path, present ? "" : " (missing)"));
        }
        String relay = session != null && session.relayHost != null ? session.relayHost : "not chosen yet";
        result.append(String.format("relay is %s:%d.\n", relay, Config.relayPort));
        return result.toString();
    }

    private static String stackTrace(Throwable e) {
        var baos = new ByteArrayOutputStream();
        e.printStackTrace(new PrintStream(baos, true, StandardCharsets.UTF_8));
        return baos.toString(StandardCharsets.UTF_8);
    }
}
