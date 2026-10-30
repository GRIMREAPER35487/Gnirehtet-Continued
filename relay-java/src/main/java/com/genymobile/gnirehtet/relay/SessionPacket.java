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

import java.nio.ByteBuffer;

/**
 * Session announcement sent by the device app as the first packet of each tunnel connection.
 * <p>
 * It identifies the VPN session across tunnel reconnections, so that the relay can keep the network connections of a
 * device whose tunnel connection dropped, and resume them when the device reconnects.
 * <p>
 * It is encoded as an IPv4 packet that relays without session support ignore (unsupported protocol):
 * <pre>
 *   IPv4 header (20 bytes): version 4, IHL 5, total length 32, protocol 253 (experimental, RFC 3692),
 *                           source and destination 0.0.0.0
 *   payload (12 bytes):     "GNRS" magic, then the 64-bit session token (never 0)
 * </pre>
 */
@SuppressWarnings("checkstyle:MagicNumber")
public final class SessionPacket {

    public static final int PROTOCOL = 253;
    public static final int LENGTH = 32;
    private static final int MAGIC = 0x474e5253; // "GNRS"

    private SessionPacket() {
        // not instantiable
    }

    /**
     * Read the session token if {@code packet} is a session announcement.
     *
     * @param packet the packet received from the device
     * @return the session token, or {@code 0} if {@code packet} is not a session announcement
     */
    public static long readToken(IPv4Packet packet) {
        if (packet.getRawLength() != LENGTH) {
            return 0;
        }
        ByteBuffer raw = packet.getRaw();
        if ((raw.get(0) & 0xff) != 0x45 || (raw.get(9) & 0xff) != PROTOCOL || raw.getInt(20) != MAGIC) {
            return 0;
        }
        return raw.getLong(24);
    }

    /**
     * Create a session announcement (used by tests; the device app has its own implementation).
     *
     * @param token the session token
     * @return the raw packet
     */
    public static ByteBuffer create(long token) {
        ByteBuffer buffer = ByteBuffer.allocate(LENGTH);
        buffer.put((byte) 0x45);
        buffer.put((byte) 0);
        buffer.putShort((short) LENGTH);
        buffer.putInt(0); // identification, flags, fragment offset
        buffer.put((byte) 0); // TTL
        buffer.put((byte) PROTOCOL);
        buffer.putShort((short) 0); // checksum
        buffer.putInt(0); // source
        buffer.putInt(0); // destination
        buffer.putInt(MAGIC);
        buffer.putLong(token);
        buffer.flip();
        return buffer;
    }
}
