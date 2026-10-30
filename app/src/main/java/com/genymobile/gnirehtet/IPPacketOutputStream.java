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

import android.util.Log;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;

/**
 * Wrapper for writing one IP packet at a time to an {@link OutputStream}.
 */
@SuppressWarnings("checkstyle:MagicNumber")
public class IPPacketOutputStream extends OutputStream {

    private static final String TAG = IPPacketOutputStream.class.getSimpleName();

    private static final int MAX_IP_PACKET_LENGTH = 1 << 16; // packet length is stored on 16 bits
    private static final int MIN_IP_PACKET_LENGTH = 20; // minimal IPv4 header length

    private final OutputStream target;
    // must always accept 1 full packet + any partial packet
    private final ByteBuffer buffer = ByteBuffer.allocate(2 * MAX_IP_PACKET_LENGTH);

    public IPPacketOutputStream(OutputStream target) {
        this.target = target;
    }

    @Override
    public void close() throws IOException {
        target.close();
    }

    @Override
    public void flush() throws IOException {
        target.flush();
    }

    @Override
    public void write(byte[] b, int off, int len) throws IOException {
        if (len > MAX_IP_PACKET_LENGTH) {
            throw new IOException("IPPacketOutputStream does not support writing more than one packet at a time");
        }
        // by design, the buffer must always have enough space for one packet
        if (BuildConfig.DEBUG && len > buffer.remaining()) {
            Log.e(TAG, len  + " must be <= than " + buffer.remaining());
            Log.e(TAG, buffer.toString());
            throw new AssertionError("Buffer is unexpectedly full");
        }
        buffer.put(b, off, len);
        buffer.flip();
        sink();
        buffer.compact();
    }

    @Override
    public void write(int b) throws IOException {
        if (!buffer.hasRemaining()) {
            throw new IOException("IPPacketOutputStream buffer is full");
        }
        buffer.put((byte) b);
        buffer.flip();
        sink();
        buffer.compact();
    }

    public int readFrom(Tunnel source) throws IOException {
        int position = buffer.position();
        int remaining = buffer.remaining();
        if (remaining == 0) {
            throw new IOException("IPPacketOutputStream buffer is full");
        }
        int r = source.receiveTo(buffer.array(), buffer.arrayOffset() + position, remaining);
        // always consume the flag, even if nothing is buffered (it must not apply to a later partial packet)
        boolean streamChanged = source.takeReceiveStreamChanged();
        if (r > 0 && position > 0 && streamChanged) {
            // the data before "position" (a partial packet) belong to the previous tunnel connection, which is lost:
            // keep only the new data, which start at a packet boundary
            Log.d(TAG, "Tunnel reconnected, dropping " + position + " buffered bytes");
            System.arraycopy(buffer.array(), buffer.arrayOffset() + position, buffer.array(), buffer.arrayOffset(), r);
            position = 0;
        }
        if (r > 0) {
            buffer.position(position + r);
            buffer.flip();
            sink();
            buffer.compact();
        }
        return r;
    }

    private void sink() throws IOException {
        // sink all packets
        while (sinkPacket()) {
            // continue
        }
    }

    private boolean sinkPacket() throws IOException {
        int version = readPacketVersion(buffer);
        if (version == -1) {
            // no packet at all
            return false;
        }
        if (version != 4) {
            Log.e(TAG, "Unsupported packet received, IP version is:" + version);
            Log.d(TAG, "Clearing buffer");
            dropBufferedData();
            return false;
        }
        int packetLength = readPacketLength(buffer);
        if (packetLength == -1) {
            // no packet
            return false;
        }
        if (packetLength < MIN_IP_PACKET_LENGTH) {
            Log.e(TAG, "Invalid packet length: " + packetLength);
            Log.d(TAG, "Clearing buffer");
            dropBufferedData();
            return false;
        }
        if (packetLength > buffer.remaining()) {
            // packet not complete yet
            return false;
        }

        target.write(buffer.array(), buffer.arrayOffset() + buffer.position(), packetLength);
        buffer.position(buffer.position() + packetLength);
        return true;
    }

    private void dropBufferedData() {
        // consume everything between position and limit, so that compact() empties the buffer
        buffer.position(buffer.limit());
    }

    /**
     * Read the packet IP version, assuming that an IP packets is stored at absolute position 0.
     *
     * @param buffer the buffer
     * @return the IP version, or {@code -1} if not available
     */
    public static int readPacketVersion(ByteBuffer buffer) {
        if (!buffer.hasRemaining()) {
            // buffer is empty
            return -1;
        }
        // version is stored in the 4 first bits
        byte versionAndIHL = buffer.get(buffer.position());
        return (versionAndIHL & 0xf0) >> 4;
    }

    /**
     * Read the packet length, assuming thatan IP packet is stored at absolute position 0.
     *
     * @param buffer the buffer
     * @return the packet length, or {@code -1} if not available
     */
    public static int readPacketLength(ByteBuffer buffer) {
        if (buffer.limit() < buffer.position() + 4) {
            // buffer does not even contains the length field
            return -1;
        }
        // packet length is 16 bits starting at offset 2
        return Binary.unsigned(buffer.getShort(buffer.position() + 2));
    }
}
