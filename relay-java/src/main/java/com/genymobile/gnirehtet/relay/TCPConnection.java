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

import java.io.IOException;
import java.net.StandardSocketOptions;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;
import java.nio.ByteBuffer;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Random;

public class TCPConnection extends AbstractConnection implements PacketSource {

    private static final String TAG = TCPConnection.class.getSimpleName();

    // same value as GnirehtetService.MTU in the client
    private static final int MTU = 0x4000;
    // 20 bytes for IP headers, 20 bytes for TCP headers
    private static final int MAX_PAYLOAD_SIZE = MTU - 20 - 20;

    private static final Random RANDOM = new Random();

    // empty packets (ACK, FIN, RST, SYN-ACK) only contain headers (IPv4 header may contain options)
    private static final int CONTROL_PACKET_BUFFER_SIZE = 128;
    // maximum number of control packets kept while the client buffer is full
    private static final int MAX_PENDING_CONTROL_PACKETS = 64;

    // unacknowledged data kept to be sent again after a tunnel reconnection (the client window is at most 64KB, since
    // window scaling is never negotiated: the TCP options are removed)
    private static final int RETRANSMISSION_BUFFER_SIZE = 2 * IPv4Packet.MAX_PACKET_LENGTH;
    // IPv4 header (with options) + TCP header (without options) + payload
    private static final int RESEND_PACKET_BUFFER_SIZE = 60 + 20 + MAX_PAYLOAD_SIZE;

    // half-open connections (waiting for an ACK from the client) expire if nothing happens
    static final long HALF_OPEN_IDLE_TIMEOUT = 60 * 1000;

    /**
     * See <a href="https://tools.ietf.org/html/rfc793#page-23">RFC793</a>.
     */
    public enum State {
        SYN_SENT,
        SYN_RECEIVED,
        ESTABLISHED,
        CLOSE_WAIT,
        LAST_ACK,
        CLOSING,
        FIN_WAIT_1,
        FIN_WAIT_2;

        public boolean isConnected() {
            return this != SYN_SENT && this != SYN_RECEIVED;
        }

        public boolean isClosed() {
            return this == FIN_WAIT_1 || this == FIN_WAIT_2 || this == CLOSING || this == LAST_ACK;
        }
    }

    private final StreamBuffer clientToNetwork = new StreamBuffer(4 * IPv4Packet.MAX_PACKET_LENGTH);
    private final Packetizer networkToClient;
    private IPv4Packet packetForClient;
    // separate buffer, so that building an empty packet never overwrites a pending packetForClient
    private final Packetizer controlToClient;
    // empty packets that could not be sent immediately, to be sent after packetForClient
    private final Deque<IPv4Packet> pendingControlPackets = new ArrayDeque<>();
    // packets to send again after the client reconnected, before anything else
    private final Deque<IPv4Packet> resendPackets = new ArrayDeque<>();
    private final RetransmissionBuffer retransmissionBuffer = new RetransmissionBuffer(RETRANSMISSION_BUFFER_SIZE);
    private long lastActivity = System.currentTimeMillis();

    private final SocketChannel channel;
    private final SelectionKey selectionKey;
    private int interests;

    private State state;
    private int synSequenceNumber;
    private int sequenceNumber;
    private int acknowledgementNumber;
    private int theirAcknowledgementNumber;
    private Integer finSequenceNumber; // null means "no FIN sent yet"
    private boolean finReceived;
    private int clientWindow;

    public TCPConnection(ConnectionId id, Client client, Selector selector, IPv4Header ipv4Header, TCPHeader tcpHeader) throws IOException {
        super(id, client);

        TCPHeader shrinkedTcpHeader = tcpHeader.copy();
        shrinkedTcpHeader.shrinkOptions(); // no TCP options

        networkToClient = new Packetizer(ipv4Header, shrinkedTcpHeader);
        networkToClient.getResponseIPv4Header().swapSourceAndDestination();
        networkToClient.getResponseTransportHeader().swapSourceAndDestination();

        controlToClient = new Packetizer(ipv4Header, shrinkedTcpHeader, CONTROL_PACKET_BUFFER_SIZE);
        controlToClient.getResponseIPv4Header().swapSourceAndDestination();
        controlToClient.getResponseTransportHeader().swapSourceAndDestination();

        SelectionHandler selectionHandler = (selectionKey) -> {
            touch();
            if (selectionKey.isValid() && selectionKey.isConnectable()) {
                processConnect();
            }
            if (selectionKey.isValid() && selectionKey.isReadable()) {
                processReceive();
            }
            if (selectionKey.isValid() && selectionKey.isWritable()) {
                processSend();
            }
            updateInterests();
        };
        channel = createChannel();
        // interests will be set on the first packet received
        // set the initial value now so that they won't need to be updated
        interests = SelectionKey.OP_CONNECT;
        selectionKey = channel.register(selector, interests, selectionHandler);
    }

    @Override
    public void disconnect() {
        logi(TAG, "Close");
        selectionKey.cancel();
        try {
            channel.close();
        } catch (IOException e) {
            loge(TAG, "Cannot close connection channel", e);
        }
    }

    private void processReceive() {
        try {
            assert packetForClient == null : "The IPv4Packet shares the networkToClient buffer, it must not be corrupted";
            int remainingClientWindow = getRemainingClientWindow();
            assert remainingClientWindow > 0 : "If remainingClientWindow is 0, then processReceive() should not have been called";
            int maxPayloadSize = Math.min(remainingClientWindow, MAX_PAYLOAD_SIZE);
            updateHeaders(TCPHeader.FLAG_ACK | TCPHeader.FLAG_PSH);
            packetForClient = networkToClient.packetize(channel, maxPayloadSize);
            if (packetForClient == null) {
                eof();
                return;
            }
            consume(this);
        } catch (IOException e) {
            loge(TAG, "Cannot read", e);
            resetConnection();
        }
    }

    private void processSend() {
        try {
            int w = clientToNetwork.writeTo(channel);
            if (w > 0) {
                acknowledgementNumber += w;

                logd(TAG, w + " bytes written to the network socket");

                if (finReceived && clientToNetwork.isEmpty()) {
                    logd(TAG, "No more pending data, process the pending FIN");
                    doHandleFin();
                } else {
                    logd(TAG, "Sending ACK " + numbers() + " to client");
                    sendEmptyPacketToClient(TCPHeader.FLAG_ACK);
                }
            }
            // w == 0: the socket send buffer is full, wait for the next OP_WRITE
        } catch (IOException e) {
            loge(TAG, "Cannot write", e);
            resetConnection();
        }
    }

    private void eof() {
        sendEmptyPacketToClient(TCPHeader.FLAG_FIN | TCPHeader.FLAG_ACK);

        finSequenceNumber = getNextSequenceNumber();
        ++sequenceNumber; // FIN counts for 1 byte
        if (state == State.CLOSE_WAIT) {
            state = State.LAST_ACK;
        } else {
            state = State.FIN_WAIT_1;
        }
        logd(TAG, "State = " + state);
    }

    private int getRemainingClientWindow() {
        // in Java, (signed) integer overflow is well-defined: it wraps around
        int remaining = theirAcknowledgementNumber + clientWindow - sequenceNumber;
        if (remaining < 0 || remaining > clientWindow) {
            // our sequence number is outside their window
            return 0;
        }
        return remaining;
    }

    @Override
    public boolean isExpired() {
        if (state == null || !isHalfOpen(state)) {
            // established connections are closed by either side, not by a timeout
            return false;
        }
        return System.currentTimeMillis() >= lastActivity + HALF_OPEN_IDLE_TIMEOUT;
    }

    static boolean isHalfOpen(State state) {
        switch (state) {
            case SYN_RECEIVED:
            case FIN_WAIT_1:
            case CLOSING:
            case LAST_ACK:
                return true;
            default:
                return false;
        }
    }

    private void touch() {
        lastActivity = System.currentTimeMillis();
    }

    private void updateHeaders(int flags) {
        TCPHeader tcpHeader = (TCPHeader) networkToClient.getResponseTransportHeader();
        tcpHeader.setFlags(flags);
        tcpHeader.setSequenceNumber(sequenceNumber);
        tcpHeader.setAcknowledgementNumber(acknowledgementNumber);
    }

    /**
     * Return the sequence number of the next byte to send, taking into account the data packet waiting for client space.
     */
    private int getNextSequenceNumber() {
        return packetForClient == null ? sequenceNumber : sequenceNumber + packetForClient.getPayloadLength();
    }

    private SocketChannel createChannel() throws IOException {
        logi(TAG, "Open");
        SocketChannel socketChannel = SocketChannel.open();
        try {
            socketChannel.configureBlocking(false);
            socketChannel.setOption(StandardSocketOptions.TCP_NODELAY, true);
            socketChannel.connect(getRewrittenDestination());
        } catch (IOException e) {
            // do not leak the channel
            socketChannel.close();
            throw e;
        }
        return socketChannel;
    }

    @Override
    public void sendToNetwork(IPv4Packet packet) {
        touch();
        handlePacket(packet);
        logd(TAG, "current ack=" + acknowledgementNumber);
        updateInterests();
    }

    private void handlePacket(IPv4Packet packet) {
        TCPHeader tcpHeader = (TCPHeader) packet.getTransportHeader();
        if (state == null) {
            handleFirstPacket(packet);
            return;
        }

        if (tcpHeader.isSyn()) {
            // the client always initiates the connection
            // at this point, any SYN packet received is duplicate
            handleDuplicateSyn(packet);
            return;
        }

        int packetSequenceNumber = tcpHeader.getSequenceNumber();
        int expectedPacket = acknowledgementNumber + clientToNetwork.size();
        if (packetSequenceNumber != expectedPacket) {
            // ignore packet already received or out-of-order, retransmission is already managed by both sides
            logw(TAG, "Ignoring packet " + packetSequenceNumber + " (acking " + tcpHeader.getAcknowledgementNumber() + "); expecting "
                    + expectedPacket + "; flags=" + tcpHeader.getFlags());
            return;
        }

        clientWindow = tcpHeader.getWindow();
        theirAcknowledgementNumber = tcpHeader.getAcknowledgementNumber();
        retransmissionBuffer.acknowledge(theirAcknowledgementNumber);

        logd(TAG, "Receiving expected paquet " + packetSequenceNumber + " (flags = " + tcpHeader.getFlags() + ")");

        if (tcpHeader.isRst()) {
            logd(TAG, "Reset requested, closing");
            close();
            return;
        }

        if (tcpHeader.isAck()) {
            logd(TAG, "Client acked " + tcpHeader.getAcknowledgementNumber());
            handleAck(packet);
        }

        if (tcpHeader.isFin()) {
            handleFin();
        }

        if (finSequenceNumber != null && tcpHeader.getAcknowledgementNumber() == finSequenceNumber + 1) {
            logd(TAG, "Received ACK of FIN");
            handleFinAck();
        }
    }

    private void handleFirstPacket(IPv4Packet packet) {
        logd(TAG, "handleFirstPacket()");
        TCPHeader tcpHeader = (TCPHeader) packet.getTransportHeader();
        if (!tcpHeader.isSyn()) {
            logw(TAG, "Unexpected first packet " + tcpHeader.getSequenceNumber() + "; acking " + tcpHeader.getAcknowledgementNumber()
                    + "; flags=" + tcpHeader.getFlags());
            sequenceNumber = tcpHeader.getAcknowledgementNumber(); // make a RST in the window client
            resetConnection();
            return;
        }

        int theirSequenceNumber = tcpHeader.getSequenceNumber();
        acknowledgementNumber = theirSequenceNumber + 1;
        synSequenceNumber = theirSequenceNumber;

        sequenceNumber = RANDOM.nextInt();
        logd(TAG, "initialized seqNum=" + sequenceNumber + "; ackNum=" + acknowledgementNumber);
        clientWindow = tcpHeader.getWindow();
        state = State.SYN_SENT;
        logd(TAG, "State = " + state);
    }

    private void handleDuplicateSyn(IPv4Packet packet) {
        TCPHeader tcpHeader = (TCPHeader) packet.getTransportHeader();
        int theirSequenceNumber = tcpHeader.getSequenceNumber();
        if (state == State.SYN_SENT) {
            // we didn't call finishConnect() yet, we can accept this packet as if it were the first SYN
            synSequenceNumber = theirSequenceNumber;
            acknowledgementNumber = theirSequenceNumber + 1;
        } else if (theirSequenceNumber != synSequenceNumber) {
            // duplicate SYN with different sequence number
            resetConnection();
        } else if (state == State.SYN_RECEIVED) {
            // the client did not receive our SYN-ACK (or it was delayed), send it again
            logd(TAG, "Duplicate SYN, re-sending SYN-ACK");
            sendEmptyPacketToClient(TCPHeader.FLAG_SYN | TCPHeader.FLAG_ACK, sequenceNumber - 1);
        }
    }

    private void handleFin() {
        logd(TAG, "Received a FIN from the client " + numbers());
        finReceived = true;
        if (clientToNetwork.isEmpty()) {
            logd(TAG, "No pending data, process the FIN immediately");
            doHandleFin();
        }
        // otherwise, the FIN will be processed once clientToNetwork is empty
    }

    private void doHandleFin() {
        ++acknowledgementNumber; // received FIN counts for 1 byte

        switch (state) {
            case ESTABLISHED:
                sendEmptyPacketToClient(TCPHeader.FLAG_FIN | TCPHeader.FLAG_ACK);
                finSequenceNumber = getNextSequenceNumber();
                ++sequenceNumber; // FIN counts for 1 byte
                // do not wait for the real network connection, switch immediately to LAST_ACK (bypass CLOSE_WAIT)
                state = State.LAST_ACK;
                logd(TAG, "State = " + state);
                break;
            case FIN_WAIT_1:
                sendEmptyPacketToClient(TCPHeader.FLAG_ACK);
                state = State.CLOSING;
                logd(TAG, "State = " + state);
                break;
            case FIN_WAIT_2:
                sendEmptyPacketToClient(TCPHeader.FLAG_ACK);
                close();
                break;
            default:
                logw(TAG, "Received FIN while state was " + state);
        }
    }

    private void handleFinAck() {
        switch (state) {
            case LAST_ACK:
            case CLOSING:
                close();
                break;
            case FIN_WAIT_1:
                state = State.FIN_WAIT_2;
                logd(TAG, "State = " + state);
                break;
            case FIN_WAIT_2:
                // do nothing
                break;
            default:
                logw(TAG, "Received FIN ACK while state was " + state);
        }
    }

    private void handleAck(IPv4Packet packet) {
        logd(TAG, "handleAck()");
        if (state == State.SYN_RECEIVED) {
            state = State.ESTABLISHED;
            logd(TAG, "State = " + state);
            return;
        }

        if (Log.isVerboseEnabled()) {
            logv(TAG, Binary.buildPacketString(packet.getRaw()));
        }

        int payloadLength = packet.getPayloadLength();
        if (payloadLength == 0) {
            // no data to transmit
            return;
        }

        if (clientToNetwork.remaining() < payloadLength) {
            logw(TAG, "Not enough space, dropping packet");
            return;
        }

        clientToNetwork.readFrom(packet.getPayload());
        // data will be ACKed once written to the network socket
    }

    private void processConnect() {
        logd(TAG, "processConnect()");
        try {
            if (!channel.finishConnect()) {
                // connection still pending, wait for the next OP_CONNECT
                return;
            }
        } catch (IOException e) {
            loge(TAG, "Cannot finish connect", e);
            // notify the client that the connection failed
            resetConnection();
            return;
        }
        logd(TAG, "SYN_RECEIVED, acking " + numbers());
        state = State.SYN_RECEIVED;
        logd(TAG, "State = " + state);
        sendEmptyPacketToClient(TCPHeader.FLAG_SYN | TCPHeader.FLAG_ACK);
        ++sequenceNumber; // SYN counts for 1 byte
        retransmissionBuffer.reset(sequenceNumber);
    }

    @Override
    public void onClientResumed() {
        if (state == null) {
            return;
        }
        // The packets sent through the previous tunnel connection may have been lost (the tunnel is normally reliable,
        // so the client will not ask for them): send again everything the client has not acknowledged yet.
        resendPackets.clear();
        if (state == State.SYN_RECEIVED) {
            resendPackets.add(copyOf(createEmptyResponsePacket(TCPHeader.FLAG_SYN | TCPHeader.FLAG_ACK, sequenceNumber - 1)));
        }
        int size = retransmissionBuffer.size();
        if (retransmissionBuffer.hasOverflowed()) {
            logw(TAG, "Unacknowledged data could not be kept, they cannot be sent again");
            size = 0;
        }
        if (size > 0) {
            Packetizer packetizer = new Packetizer(controlToClient.getResponseIPv4Header(), controlToClient.getResponseTransportHeader(),
                    RESEND_PACKET_BUFFER_SIZE);
            TCPHeader tcpHeader = (TCPHeader) packetizer.getResponseTransportHeader();
            for (int offset = 0; offset < size; offset += MAX_PAYLOAD_SIZE) {
                int length = Math.min(MAX_PAYLOAD_SIZE, size - offset);
                tcpHeader.setFlags(TCPHeader.FLAG_ACK | TCPHeader.FLAG_PSH);
                tcpHeader.setSequenceNumber(retransmissionBuffer.getBase() + offset);
                tcpHeader.setAcknowledgementNumber(acknowledgementNumber);
                resendPackets.add(copyOf(packetizer.packetize(retransmissionBuffer.copy(offset, length))));
            }
        }
        if (finSequenceNumber != null && (state == State.FIN_WAIT_1 || state == State.CLOSING || state == State.LAST_ACK)) {
            // our FIN has not been acknowledged
            resendPackets.add(copyOf(createEmptyResponsePacket(TCPHeader.FLAG_FIN | TCPHeader.FLAG_ACK, finSequenceNumber)));
        }
        if (resendPackets.isEmpty()) {
            // nothing lost on our side, but tell the client our state, so that it sends again what we did not receive
            resendPackets.add(copyOf(createEmptyResponsePacket(TCPHeader.FLAG_ACK, sequenceNumber)));
        }
        logd(TAG, "Client resumed, sending again " + size + " unacknowledged bytes " + numbers());
        consume(this);
    }

    private static IPv4Packet copyOf(IPv4Packet packet) {
        return new IPv4Packet(Binary.copy(packet.getRaw()));
    }

    private void resetConnection() {
        logd(TAG, "Resetting connection");
        state = null;
        sendEmptyPacketToClient(TCPHeader.FLAG_RST);
        close();
    }

    private IPv4Packet createEmptyResponsePacket(int flags, int seq) {
        TCPHeader tcpHeader = (TCPHeader) controlToClient.getResponseTransportHeader();
        tcpHeader.setFlags(flags);
        tcpHeader.setSequenceNumber(seq);
        tcpHeader.setAcknowledgementNumber(acknowledgementNumber);
        IPv4Packet packet = controlToClient.packetizeEmptyPayload();
        logd(TAG, "Forging empty response (flags=" + flags + ") " + numbers());
        if (Log.isVerboseEnabled()) {
            logd(TAG, Binary.buildPacketString(packet.getRaw()));
        }
        if ((flags & TCPHeader.FLAG_ACK) != 0) {
            logd(TAG, "Acking " + numbers());
        }
        return packet;
    }

    private void sendEmptyPacketToClient(int flags) {
        sendEmptyPacketToClient(flags, getNextSequenceNumber());
    }

    private void sendEmptyPacketToClient(int flags, int seq) {
        IPv4Packet packet = createEmptyResponsePacket(flags, seq);
        if (packetForClient == null && pendingControlPackets.isEmpty() && resendPackets.isEmpty() && sendToClient(packet)) {
            return;
        }
        // keep the order with the pending packets, and never lose a control packet if the client buffer is full
        if (pendingControlPackets.size() >= MAX_PENDING_CONTROL_PACKETS) {
            logw(TAG, "Too many pending control packets, dropping the oldest");
            pendingControlPackets.pollFirst();
        }
        // the controlToClient buffer is reused, store a copy
        pendingControlPackets.addLast(new IPv4Packet(Binary.copy(packet.getRaw())));
        consume(this);
    }

    protected void updateInterests() {
        if (!selectionKey.isValid()) {
            return;
        }
        int interestOps = 0;
        if (mayRead()) {
            interestOps |= SelectionKey.OP_READ;
        }
        if (mayWrite()) {
            interestOps |= SelectionKey.OP_WRITE;
        }
        if (mayConnect()) {
            interestOps |= SelectionKey.OP_CONNECT;
        }
        if (interests != interestOps) {
            // interests must be changed
            interests = interestOps;
            selectionKey.interestOps(interestOps);
        }
    }

    private boolean mayRead() {
        if (!state.isConnected() || state.isClosed()) {
            return false;
        }
        if (packetForClient != null || !pendingControlPackets.isEmpty() || !resendPackets.isEmpty()) {
            // a packet is already pending
            return false;
        }
        return getRemainingClientWindow() > 0;
    }

    private boolean mayWrite() {
        return !clientToNetwork.isEmpty();
    }

    private boolean mayConnect() {
        return state == State.SYN_SENT;
    }

    private String numbers() {
        return "(seq=" + sequenceNumber + ", ack=" + acknowledgementNumber + ")";
    }

    @Override
    public IPv4Packet get() {
        if (!resendPackets.isEmpty()) {
            // packets lost with the previous tunnel connection come first
            return resendPackets.peekFirst();
        }
        if (packetForClient != null) {
            // TODO update only when necessary
            updateAcknowledgementNumber(packetForClient);
            return packetForClient;
        }
        // control packets queued while the client buffer was full (or after packetForClient)
        return pendingControlPackets.peekFirst();
    }

    private void updateAcknowledgementNumber(IPv4Packet packet) {
        TCPHeader tcpHeader = (TCPHeader) packet.getTransportHeader();
        tcpHeader.setAcknowledgementNumber(acknowledgementNumber);
        packet.computeChecksums();
    }

    @Override
    public void next() {
        if (!resendPackets.isEmpty()) {
            resendPackets.pollFirst();
            updateInterests();
            return;
        }
        if (packetForClient == null) {
            IPv4Packet control = pendingControlPackets.pollFirst();
            assert control != null : "next() called without any pending packet";
            logd(TAG, "Pending control packet sent to client");
            updateInterests();
            return;
        }
        logd(TAG, "Packet (" + packetForClient.getPayloadLength() + " bytes) sent to client " + numbers());
        if (Log.isVerboseEnabled()) {
            logv(TAG, Binary.buildPacketString(packetForClient.getRaw()));
        }
        retransmissionBuffer.append(packetForClient.getPayload());
        sequenceNumber += packetForClient.getPayloadLength();
        packetForClient = null;
        updateInterests();
    }
}
