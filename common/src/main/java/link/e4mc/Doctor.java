package link.e4mc;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
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
        var session = E4mcClient.session;
        if (session != null && session.failureCause != null) {
            result.append(stackTrace(session.failureCause));
            result.append("\n");
        } else {
            result.append("none recorded.\n");
        }
        result.append("natives mirror test results:\n");
        try {
            var request = HttpRequest.newBuilder(new URI(System.getProperty("link.e4mc.native_url")))
                    .method("HEAD", HttpRequest.BodyPublishers.noBody())
                    .build();
            int status = HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.discarding()).statusCode();
            result.append(status == 200 ? "no issues found.\n" : "status code was not 200, it was: " + status + "\n");
        } catch (Exception e) {
            result.append("exception during request:\n");
            result.append(stackTrace(e));
            result.append("\n");
        }
        String relay = session != null && session.relayHost != null ? session.relayHost : "not chosen yet";
        result.append(String.format("relay is %s:%d.\n", relay, Config.INSTANCE.relayPort.value()));
        return result.toString();
    }

    private static String stackTrace(Throwable e) {
        var baos = new ByteArrayOutputStream();
        e.printStackTrace(new PrintStream(baos, true, StandardCharsets.UTF_8));
        return baos.toString(StandardCharsets.UTF_8);
    }
}
