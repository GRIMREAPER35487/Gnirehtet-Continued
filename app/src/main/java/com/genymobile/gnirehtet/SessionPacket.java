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

package com.genymobile.gnirehtet;

import java.nio.ByteBuffer;

/**
 * Session announcement, sent to the relay as the first packet of each tunnel connection.
 * <p>
 * Same format as the relay's {@code com.genymobile.gnirehtet.relay.SessionPacket}: an IPv4 packet with protocol 253
 * (ignored by relays without session support) whose payload is "GNRS" followed by the 64-bit session token.
 */
@SuppressWarnings("checkstyle:MagicNumber")
public final class SessionPacket {

    public static final int PROTOCOL = 253;
    public static final int LENGTH = 32;
    private static final int MAGIC = 0x474e5253; // "GNRS"

    private SessionPacket() {
        // not instantiable
    }

    public static byte[] create(long token) {
        ByteBuffer buffer = ByteBuffer.allocate(LENGTH);
        buffer.put((byte) 0x45); // version 4, IHL 5
        buffer.put((byte) 0); // ToS
        buffer.putShort((short) LENGTH);
        buffer.putInt(0); // identification, flags, fragment offset
        buffer.put((byte) 0); // TTL
        buffer.put((byte) PROTOCOL);
        buffer.putShort((short) 0); // checksum
        buffer.putInt(0); // source
        buffer.putInt(0); // destination
        buffer.putInt(MAGIC);
        buffer.putLong(token);
        return buffer.array();
    }
}
