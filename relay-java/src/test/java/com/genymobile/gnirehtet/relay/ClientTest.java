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

import org.junit.Assert;
import org.junit.Test;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;

@SuppressWarnings("checkstyle:MagicNumber")
public class ClientTest {

    private static final int BIG_PACKET_LENGTH = 60000;

    private static IPv4Packet createBigPacket() {
        ByteBuffer buffer = ByteBuffer.allocate(BIG_PACKET_LENGTH);
        buffer.put((byte) ((4 << 4) | 5)); // versionAndIHL
        buffer.put((byte) 0); // ToS
        buffer.putShort((short) BIG_PACKET_LENGTH); // total length
        buffer.putInt(0); // IdFlagsFragmentOffset
        buffer.put((byte) 0); // TTL
        buffer.put((byte) 17); // protocol (UDP)
        buffer.putShort((short) 0); // checksum
        buffer.putInt(0x12345678); // source address
        buffer.putInt(0x42424242); // destination address
        buffer.putShort((short) 1234); // source port
        buffer.putShort((short) 5678); // destination port
        buffer.putShort((short) (BIG_PACKET_LENGTH - 20)); // length
        buffer.putShort((short) 0); // checksum
        buffer.position(0);
        return new IPv4Packet(buffer);
    }

    private static class CountingSource implements PacketSource {
        private final IPv4Packet packet = createBigPacket();
        private int remaining;
        private int sent;
        private int getCalls;

        CountingSource(int count) {
            remaining = count;
        }

        @Override
        public IPv4Packet get() {
            ++getCalls;
            return remaining > 0 ? packet : null;
        }

        @Override
        public void next() {
            --remaining;
            ++sent;
        }
    }

    @Test
    public void testConsumeAllPackets() throws IOException {
        try (Selector selector = Selector.open(); SocketChannel channel = SocketChannel.open()) {
            channel.configureBlocking(false);
            Client client = new Client(selector, channel, c -> { });
            CountingSource source = new CountingSource(3);
            client.consume(source);
            Assert.assertEquals(3, source.sent);
        }
    }

    @Test
    public void testPendingSourceRemoval() throws IOException {
        try (Selector selector = Selector.open(); SocketChannel channel = SocketChannel.open()) {
            channel.configureBlocking(false);
            Client client = new Client(selector, channel, c -> { });
            // more packets than the client buffer can hold
            CountingSource source = new CountingSource(100);
            client.consume(source);
            Assert.assertTrue("The client buffer must be full", source.sent < 100);

            // the source is pending: consume() must not send anything out of order
            int getCalls = source.getCalls;
            client.consume(source);
            Assert.assertEquals(getCalls, source.getCalls);

            // once removed (connection closed), it is not pending anymore
            client.removePendingPacketSource(source);
            client.consume(source);
            Assert.assertTrue(source.getCalls > getCalls);
        }
    }
}
