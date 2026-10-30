/*
 * Copyright (C) 2017 Genymobile
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.genymobile.gnirehtet.relay;

import org.junit.AfterClass;
import org.junit.Assert;
import org.junit.BeforeClass;
import org.junit.Test;

import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;

/**
 * End-to-end test: a fake device opens a TCP connection through a real relay, loses its tunnel connection, reconnects,
 * and the TCP connection must survive.
 */
@SuppressWarnings("checkstyle:MagicNumber")
public class SessionResumeTest {

    private static final int DEVICE_IP = 0x0a000002; // 10.0.0.2
    private static final int LOCALHOST = 0x7f000001; // 127.0.0.1
    private static final int TIMEOUT_MS = 5000;

    private static int relayPort;
    private static Thread relayThread;

    @BeforeClass
    public static void startRelay() throws Exception {
        try (ServerSocket probe = new ServerSocket(0)) {
            relayPort = probe.getLocalPort();
        }
        relayThread = new Thread(() -> {
            try {
                new Relay(relayPort).run();
            } catch (IOException e) {
                throw new RuntimeException(e);
            }
        });
        relayThread.setDaemon(true);
        relayThread.start();
        // wait for the relay to listen
        long deadline = System.currentTimeMillis() + TIMEOUT_MS;
        while (true) {
            try (Socket s = new Socket(InetAddress.getLoopbackAddress(), relayPort)) {
                break;
            } catch (IOException e) {
                if (System.currentTimeMillis() > deadline) {
                    throw e;
                }
                Thread.sleep(50);
            }
        }
    }

    @AfterClass
    public static void stopRelay() {
        relayThread.interrupt();
    }

    /**
     * The device side of the tunnel.
     */
    private static final class FakeDevice implements AutoCloseable {
        private final Socket socket;
        private final DataInputStream in;
        private final OutputStream out;

        FakeDevice(long sessionToken) throws IOException {
            socket = new Socket(InetAddress.getLoopbackAddress(), relayPort);
            socket.setSoTimeout(TIMEOUT_MS);
            in = new DataInputStream(socket.getInputStream());
            out = socket.getOutputStream();
            in.readInt(); // client id
            if (sessionToken != 0) {
                send(SessionPacket.create(sessionToken));
            }
        }

        void send(ByteBuffer packet) throws IOException {
            out.write(packet.array(), packet.arrayOffset() + packet.position(), packet.remaining());
            out.flush();
        }

        IPv4Packet receive() throws IOException {
            byte[] header = new byte[4];
            in.readFully(header);
            int length = ((header[2] & 0xff) << 8) | (header[3] & 0xff);
            byte[] raw = new byte[length];
            System.arraycopy(header, 0, raw, 0, 4);
            in.readFully(raw, 4, length - 4);
            return new IPv4Packet(ByteBuffer.wrap(raw));
        }

        /**
         * Receive packets until one carries a payload (skipping pure ACKs).
         */
        IPv4Packet receiveData() throws IOException {
            while (true) {
                IPv4Packet packet = receive();
                if (packet.getPayloadLength() > 0) {
                    return packet;
                }
            }
        }

        @Override
        public void close() throws IOException {
            socket.close();
        }
    }

    private static ByteBuffer tcp(int srcPort, int dstPort, int seq, int ack, int flags, byte[] payload) {
        int length = 20 + 20 + payload.length;
        ByteBuffer buffer = ByteBuffer.allocate(length);
        buffer.put((byte) 0x45);
        buffer.put((byte) 0);
        buffer.putShort((short) length);
        buffer.putInt(0);
        buffer.put((byte) 64); // TTL
        buffer.put((byte) 6); // TCP
        buffer.putShort((short) 0); // checksum (not checked by the relay)
        buffer.putInt(DEVICE_IP);
        buffer.putInt(LOCALHOST);
        buffer.putShort((short) srcPort);
        buffer.putShort((short) dstPort);
        buffer.putInt(seq);
        buffer.putInt(ack);
        buffer.putShort((short) ((5 << 12) | flags));
        buffer.putShort((short) 0xffff); // window
        buffer.putShort((short) 0); // checksum
        buffer.putShort((short) 0); // urgent pointer
        buffer.put(payload);
        buffer.flip();
        return buffer;
    }

    private static String payloadOf(IPv4Packet packet) {
        ByteBuffer payload = packet.getPayload();
        byte[] bytes = new byte[payload.remaining()];
        payload.get(bytes);
        return new String(bytes, StandardCharsets.US_ASCII);
    }

    private static byte[] ascii(String s) {
        return s.getBytes(StandardCharsets.US_ASCII);
    }

    private static String readAscii(InputStream in, int length) throws IOException {
        byte[] bytes = new byte[length];
        new DataInputStream(in).readFully(bytes);
        return new String(bytes, StandardCharsets.US_ASCII);
    }

    @Test(timeout = 30000)
    public void testTcpConnectionSurvivesTunnelReconnection() throws Exception {
        long token = 0x1234567890abcdefL;
        try (ServerSocket server = new ServerSocket(0, 1, InetAddress.getLoopbackAddress())) {
            server.setSoTimeout(TIMEOUT_MS);
            int serverPort = server.getLocalPort();
            int devicePort = 40000;

            FakeDevice device = new FakeDevice(token);
            int seq = 1000;
            device.send(tcp(devicePort, serverPort, seq, 0, TCPHeader.FLAG_SYN, new byte[0]));
            try (Socket serverSide = server.accept()) {
                serverSide.setSoTimeout(TIMEOUT_MS);
                IPv4Packet synAck = device.receive();
                TCPHeader synAckHeader = (TCPHeader) synAck.getTransportHeader();
                Assert.assertTrue(synAckHeader.isSyn() && synAckHeader.isAck());
                int relaySeq = synAckHeader.getSequenceNumber() + 1;
                ++seq;
                device.send(tcp(devicePort, serverPort, seq, relaySeq, TCPHeader.FLAG_ACK, new byte[0]));

                // the server sends data, the device receives it but the tunnel drops before the device acknowledges it
                serverSide.getOutputStream().write(ascii("hello"));
                IPv4Packet data = device.receiveData();
                Assert.assertEquals("hello", payloadOf(data));
                Assert.assertEquals(relaySeq, ((TCPHeader) data.getTransportHeader()).getSequenceNumber());
                device.close();

                // the device reconnects with the same session token
                device = new FakeDevice(token);

                // the unacknowledged data are sent again
                IPv4Packet resent = device.receiveData();
                Assert.assertEquals("hello", payloadOf(resent));
                Assert.assertEquals(relaySeq, ((TCPHeader) resent.getTransportHeader()).getSequenceNumber());
                relaySeq += 5;

                // the connection still works in both directions
                device.send(tcp(devicePort, serverPort, seq, relaySeq, TCPHeader.FLAG_ACK | TCPHeader.FLAG_PSH, ascii("ping")));
                Assert.assertEquals("ping", readAscii(serverSide.getInputStream(), 4));
                serverSide.getOutputStream().write(ascii("world"));
                IPv4Packet next = device.receiveData();
                Assert.assertEquals("world", payloadOf(next));
                Assert.assertEquals(relaySeq, ((TCPHeader) next.getTransportHeader()).getSequenceNumber());
            } finally {
                device.close();
            }
        }
    }

    @Test(timeout = 30000)
    public void testConnectionsClosedWithoutSession() throws Exception {
        try (ServerSocket server = new ServerSocket(0, 1, InetAddress.getLoopbackAddress())) {
            server.setSoTimeout(TIMEOUT_MS);
            int serverPort = server.getLocalPort();
            int devicePort = 40001;

            FakeDevice device = new FakeDevice(0); // an app without session support
            device.send(tcp(devicePort, serverPort, 2000, 0, TCPHeader.FLAG_SYN, new byte[0]));
            try (Socket serverSide = server.accept()) {
                serverSide.setSoTimeout(TIMEOUT_MS);
                device.receive(); // SYN-ACK
                device.close();
                // the relay closes the network connection with the client
                Assert.assertEquals(-1, serverSide.getInputStream().read());
            } finally {
                device.close();
            }
        }
    }
}
