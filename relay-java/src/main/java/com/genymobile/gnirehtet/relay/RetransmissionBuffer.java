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
 * Keeps the TCP payload sent to the client until the client acknowledges it.
 * <p>
 * The tunnel to the client is normally reliable, so the relay never retransmits. But when the tunnel connection drops,
 * the data in flight are lost: once the client reconnects, they are sent again from this buffer.
 * <p>
 * The data are identified by TCP sequence numbers: the buffer contains the bytes in [{@link #getBase()},
 * {@link #getBase()} + {@link #size()}).
 */
public class RetransmissionBuffer {

    private final byte[] data;
    private int head; // index of the byte at sequence number "base"
    private int size;
    private int base;
    private boolean overflowed;

    public RetransmissionBuffer(int capacity) {
        data = new byte[capacity];
    }

    /**
     * Restart at the given sequence number (the next byte to be appended).
     *
     * @param sequenceNumber the sequence number of the next byte
     */
    public void reset(int sequenceNumber) {
        head = 0;
        size = 0;
        base = sequenceNumber;
        overflowed = false;
    }

    public int getBase() {
        return base;
    }

    public int size() {
        return size;
    }

    /**
     * Indicate whether some unacknowledged data could not be kept (they cannot be sent again).
     *
     * @return {@code true} if the buffer overflowed
     */
    public boolean hasOverflowed() {
        return overflowed;
    }

    /**
     * Append data sent to the client.
     *
     * @param payload the data (consumed)
     */
    public void append(ByteBuffer payload) {
        int length = payload.remaining();
        if (overflowed || size + length > data.length) {
            // should not happen: the client window (at most 64KB) limits the unacknowledged data
            overflowed = true;
            payload.position(payload.limit());
            return;
        }
        int tail = (head + size) % data.length;
        int first = Math.min(length, data.length - tail);
        payload.get(data, tail, first);
        payload.get(data, 0, length - first);
        size += length;
    }

    /**
     * Discard the data acknowledged by the client.
     *
     * @param acknowledgementNumber the acknowledgement number received from the client
     */
    public void acknowledge(int acknowledgementNumber) {
        // in Java, (signed) integer overflow is well-defined: it wraps around like TCP sequence numbers
        int acked = acknowledgementNumber - base;
        if (acked <= 0 || acked > size + 1) {
            // old acknowledgement, or out of range (the +1 is for a FIN, which is not stored here)
            return;
        }
        int n = Math.min(acked, size);
        head = (head + n) % data.length;
        size -= n;
        base += n;
    }

    /**
     * Copy unacknowledged data.
     *
     * @param offset the offset from {@link #getBase()}
     * @param length the number of bytes
     * @return a buffer containing the data
     */
    public ByteBuffer copy(int offset, int length) {
        if (offset < 0 || length < 0 || offset + length > size) {
            throw new IllegalArgumentException("Out of range: offset=" + offset + ", length=" + length + ", size=" + size);
        }
        byte[] result = new byte[length];
        int start = (head + offset) % data.length;
        int first = Math.min(length, data.length - start);
        System.arraycopy(data, start, result, 0, first);
        System.arraycopy(data, 0, result, first, length - first);
        return ByteBuffer.wrap(result);
    }
}
