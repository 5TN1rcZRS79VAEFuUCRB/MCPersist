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
        result.append("natives CDN test results:\n");
        try {
            var httpClient = HttpClient.newHttpClient();
            var request = HttpRequest
                    .newBuilder(new URI("https://natives.e4mc.link/doctor-test-target"))
                    .build();
            var response = httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            var exceptional = false;
            if (response.statusCode() != 200) {
                exceptional = true;
                result.append("status code was not 200, it was: ");
                result.append(response.statusCode());
                result.append("\n");
            }
            if (!response.body().equals("if you can read this, e4mc natives are available. qmqj8c13nzdr0kd10gihcila")) {
                exceptional = true;
                result.append("response was unexpected, got: ");
                result.append(response.body());
                result.append("\n");
            }
            if (!exceptional) {
                result.append("no issues found.\n");
            }
        } catch (Exception e) {
            result.append("exception during request:\n");
            result.append(stackTrace(e));
            result.append("\n");
        }
        result.append(String.format("relay is %s:%d.\n", Config.INSTANCE.relayHost.value(), Config.INSTANCE.relayPort.value()));
        return result.toString();
    }

    private static String stackTrace(Throwable e) {
        var baos = new ByteArrayOutputStream();
        e.printStackTrace(new PrintStream(baos, true, StandardCharsets.UTF_8));
        return baos.toString(StandardCharsets.UTF_8);
    }
}
