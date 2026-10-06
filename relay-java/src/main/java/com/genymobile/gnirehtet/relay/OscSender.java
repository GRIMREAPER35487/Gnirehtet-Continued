package com.genymobile.gnirehtet.relay;

import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.DatagramChannel;
import java.nio.charset.StandardCharsets;

public final class OscSender {
    private static final String TAG = "OscSender";
    private static final String VRC_OSC_IP = "127.0.0.1";
    private static final int VRC_OSC_PORT = 9000;

    private OscSender() {
        // utility class
    }

    public static void sendVrcChatboxMessage(String message) {
        try {
            // OSC Address: /chatbox/input (14 chars) -> padded to 16 bytes
            byte[] addressBytes = "/chatbox/input\0\0".getBytes(StandardCharsets.US_ASCII);

            // OSC Type tags: ,sTT (String, True, True) -> 4 chars, needs 4 null bytes to align to 8
            byte[] typeTagBytes = ",sTT\0\0\0\0".getBytes(StandardCharsets.US_ASCII);

            // Argument: Message string (null-terminated and padded to multiple of 4 bytes)
            byte[] msgBytes = message.getBytes(StandardCharsets.UTF_8);
            int msgLen = msgBytes.length;
            int msgPaddedLen = (msgLen + 4) & ~3; // ensures at least 1 null byte and aligned to 4
            byte[] msgPadded = new byte[msgPaddedLen];
            System.arraycopy(msgBytes, 0, msgPadded, 0, msgLen);

            // Combine into single OSC packet buffer
            ByteBuffer buffer = ByteBuffer.allocate(addressBytes.length + typeTagBytes.length + msgPadded.length);
            buffer.put(addressBytes);
            buffer.put(typeTagBytes);
            buffer.put(msgPadded);
            buffer.flip();

            // Send via UDP to VRChat OSC port
            DatagramChannel channel = DatagramChannel.open();
            channel.send(buffer, new InetSocketAddress(VRC_OSC_IP, VRC_OSC_PORT));
            channel.close();
            Log.d(TAG, "Sent VRChat OSC message: " + message);
        } catch (Exception e) {
            // Log warning but do not crash the server
            Log.w(TAG, "Failed to send OSC message to VRChat", e);
        }
    }
}
