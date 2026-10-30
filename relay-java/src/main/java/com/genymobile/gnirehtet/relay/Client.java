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
import java.nio.ByteBuffer;
import java.nio.channels.ClosedChannelException;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;

/**
 * A device connected to the relay.
 * <p>
 * If the device app announced a session token (see {@link SessionPacket}), losing the tunnel connection does not close
 * the client: it is <em>detached</em>, and its network connections are kept for {@link #RESUME_TIMEOUT_MS}, so that the
 * app can reconnect and resume them (the connections of the apps on the device then survive the reconnection).
 */
public class Client {

    private static final String TAG = Client.class.getSimpleName();

    /**
     * How long the connections of a detached client are kept, waiting for the device to reconnect.
     */
    public static final long RESUME_TIMEOUT_MS = 60 * 1000;

    /**
     * Notified of the session events of a client.
     */
    public interface SessionManager {
        /**
         * Called when a client announces its session token.
         *
         * @param client the client
         * @param token the session token
         * @return {@code true} if the tunnel connection of {@code client} has been handed over to another client
         * (resumed session), in which case {@code client} must not be used anymore
         */
        boolean onSessionToken(Client client, long token);
    }

    private static int nextId = 0;

    private int id;
    private SocketChannel clientChannel; // null while detached
    private SelectionKey selectionKey; // null while detached
    private final SelectionHandler selectionHandler;
    private final CloseListener<Client> closeListener;
    private final SessionManager sessionManager;
    private int interests;

    private IPv4PacketBuffer clientToNetwork = new IPv4PacketBuffer();
    private final StreamBuffer networkToClient = new StreamBuffer(64 * IPv4Packet.MAX_PACKET_LENGTH);
    private final Router router;

    private final List<PacketSource> pendingPacketSources = new ArrayList<>();

    // store the remaining bytes of "id" to send to the client before relaying any data
    private ByteBuffer pendingIdBuffer;

    private long sessionToken; // 0 if the app did not announce any session (its connections die with the tunnel)
    private long detachedSince = -1; // -1 while attached
    private boolean handedOver; // the tunnel connection now belongs to another client (resumed session)
    private boolean closed;

    public Client(Selector selector, SocketChannel clientChannel, CloseListener<Client> closeListener) throws ClosedChannelException {
        this(selector, clientChannel, closeListener, null);
    }

    public Client(Selector selector, SocketChannel clientChannel, CloseListener<Client> closeListener, SessionManager sessionManager)
            throws ClosedChannelException {
        id = nextId++;
        this.clientChannel = clientChannel;
        this.closeListener = closeListener;
        this.sessionManager = sessionManager;
        router = new Router(this, selector);
        pendingIdBuffer = createIntBuffer(id);

        selectionHandler = (selectionKey) -> {
            if (selectionKey.isValid() && selectionKey.isWritable()) {
                processSend();
            }
            if (isAttached() && selectionKey.isValid() && selectionKey.isReadable()) {
                processReceive();
            }
            if (isAttached() && selectionKey.isValid()) {
                updateInterests();
            }
        };
        // on start, we are interested only in writing (we must first send the client id)
        interests = SelectionKey.OP_WRITE;
        selectionKey = clientChannel.register(selector, interests, selectionHandler);
    }

    private static ByteBuffer createIntBuffer(int value) {
        final int intSize = 4;
        ByteBuffer buffer = ByteBuffer.allocate(intSize);
        buffer.putInt(value);
        buffer.flip();
        return buffer;
    }

    public int getId() {
        return id;
    }

    public Router getRouter() {
        return router;
    }

    public long getSessionToken() {
        return sessionToken;
    }

    void setSessionToken(long sessionToken) {
        this.sessionToken = sessionToken;
    }

    /**
     * Indicate whether the client currently has a tunnel connection.
     *
     * @return {@code true} if attached
     */
    public boolean isAttached() {
        return clientChannel != null && !handedOver && !closed;
    }

    /**
     * Indicate whether this client has been detached for too long to be resumed.
     *
     * @return {@code true} if the client must be closed
     */
    public boolean isResumeExpired() {
        return detachedSince != -1 && System.currentTimeMillis() >= detachedSince + RESUME_TIMEOUT_MS;
    }

    private String closeReason = "Unknown reason";

    private void processReceive() {
        if (!read()) {
            onTunnelLost(closeReason);
            return;
        }
        pushToNetwork();
    }

    private void processSend() {
        if (mustSendId()) {
            if (!sendId()) {
                onTunnelLost(closeReason);
            }
            return;
        }
        if (!write()) {
            onTunnelLost(closeReason);
            return;
        }
        processPending();
    }

    private boolean read() {
        try {
            int r = clientToNetwork.readFrom(clientChannel);
            if (r == -1) {
                closeReason = "EOF (headset client closed connection or USB cable unplugged)";
                return false;
            }
            return true;
        } catch (IOException e) {
            closeReason = "Read exception: " + e.getMessage();
            Log.e(TAG, "Cannot read", e);
            return false;
        }
    }

    private boolean write() {
        try {
            while (!networkToClient.isEmpty()) {
                int r = networkToClient.writeTo(clientChannel);
                if (r == -1) {
                    closeReason = "EOF on write";
                    return false;
                }
                if (r == 0) {
                    // Socket buffer full (would block), remaining data will be sent on next writable event
                    break;
                }
            }
            return true;
        } catch (IOException e) {
            closeReason = "Write exception: " + e.getMessage();
            Log.e(TAG, "Cannot write", e);
            return false;
        }
    }

    private boolean mustSendId() {
        return pendingIdBuffer != null && pendingIdBuffer.hasRemaining();
    }

    private boolean sendId() {
        assert mustSendId();
        try {
            if (clientChannel.write(pendingIdBuffer) == -1) {
                closeReason = "Cannot write client id (EOF)";
                Log.w(TAG, "Cannot write client id #" + id + " (EOF)");
                return false;
            }
            if (!pendingIdBuffer.hasRemaining()) {
                // we don't need this buffer anymore, release it
                Log.d(TAG, "Client id #" + id + " sent to client");
                pendingIdBuffer = null;
            }
            return true;
        } catch (IOException e) {
            closeReason = "Send ID exception: " + e.getMessage();
            Log.e(TAG, "Cannot write client id #" + id, e);
            return false;
        }
    }

    private void pushToNetwork() {
        IPv4Packet packet;
        while (isAttached() && (packet = clientToNetwork.asIPv4Packet()) != null) {
            long token = SessionPacket.readToken(packet);
            if (token != 0) {
                clientToNetwork.next();
                if (sessionManager != null && sessionManager.onSessionToken(this, token)) {
                    // the session has been resumed by another client, which now handles this tunnel connection
                    return;
                }
                continue;
            }
            router.sendToNetwork(packet);
            clientToNetwork.next();
        }
    }

    private void onTunnelLost(String reason) {
        if (sessionToken == 0 || sessionManager == null) {
            // no session to resume (app without session support)
            close(reason);
            return;
        }
        // the "disconnected" wording is parsed by the watchdog
        Log.i(TAG, "Client #" + id + " disconnected (" + reason + "); keeping its connections for " + RESUME_TIMEOUT_MS / 1000
                + "s, waiting for the device to reconnect");
        detachChannel();
        detachedSince = System.currentTimeMillis();
    }

    /**
     * Release the current tunnel connection, but keep the network connections.
     */
    private void detachChannel() {
        if (selectionKey != null) {
            selectionKey.cancel();
            selectionKey = null;
        }
        if (clientChannel != null) {
            try {
                clientChannel.close();
            } catch (IOException e) {
                Log.e(TAG, "Cannot close client connection", e);
            }
            clientChannel = null;
        }
        // The stream state is tied to the tunnel connection: drop the partial packet received, and the data not written
        // yet (it may start in the middle of a packet). TCP data are sent again on resume (see TCPConnection).
        clientToNetwork = new IPv4PacketBuffer();
        networkToClient.clear();
        pendingIdBuffer = null;
    }

    /**
     * Take over the tunnel connection of {@code newClient}, which announced the session token of this client.
     *
     * @param newClient the client owning the new tunnel connection
     */
    void resume(Client newClient) {
        if (clientChannel != null) {
            // the previous tunnel connection is still open on our side, but the device opened a new one
            Log.i(TAG, "Client #" + id + " disconnected (replaced by a new tunnel connection)");
            detachChannel();
        }
        Log.i(TAG, "Client #" + newClient.id + " resumed the session of client #" + id + " (" + router.size() + " connections)");

        clientChannel = newClient.clientChannel;
        selectionKey = newClient.selectionKey;
        clientToNetwork = newClient.clientToNetwork; // may contain packets received after the session token
        pendingIdBuffer = null; // already sent by newClient
        newClient.handedOver = true;
        newClient.clientChannel = null;
        newClient.selectionKey = null;

        // from now on, this session is known by the id of its current tunnel connection (logs, watchdog)
        id = newClient.id;
        detachedSince = -1;
        selectionKey.attach(selectionHandler);
        interests = -1; // force the update

        // send again what may have been lost with the previous tunnel connection
        router.onClientResumed();
        processPending();
        pushToNetwork();
        if (isAttached()) {
            updateInterests();
        }
    }

    /**
     * Close the client and all its connections.
     *
     * @param reason the reason, for logging
     */
    public void close(String reason) {
        if (closed) {
            return;
        }
        Log.i(TAG, "Client #" + id + " connection dropped. Reason: " + reason);
        if (clientChannel != null) {
            detachChannel();
        }
        closed = true;
        router.clear();
        closeListener.onClosed(this);
    }

    private void updateInterests() {
        if (!isAttached()) {
            return;
        }
        int interestOps = SelectionKey.OP_READ; // we always want to read
        if (!networkToClient.isEmpty() || mustSendId()) {
            interestOps |= SelectionKey.OP_WRITE;
        }
        if (interests != interestOps) {
            // interests must be changed
            interests = interestOps;
            selectionKey.interestOps(interestOps);
        }
    }

    public boolean sendToClient(IPv4Packet packet) {
        if (!isAttached()) {
            // no tunnel connection: the connections keep their packets until the client is resumed
            return false;
        }
        if (networkToClient.remaining() < packet.getRawLength()) {
            Log.w(TAG, "Client buffer full");
            return false;
        }
        networkToClient.readFrom(packet.getRaw());
        updateInterests();
        return true;
    }

    public void consume(PacketSource source) {
        if (pendingPacketSources.contains(source)) {
            // already waiting for space, its packets will be pulled in order by processPending()
            return;
        }
        if (!drain(source)) {
            pendingPacketSources.add(source);
        }
    }

    /**
     * Send all the packets available from {@code source}.
     *
     * @param source the packet source
     * @return {@code true} if all packets have been sent, {@code false} if the client buffer is full
     */
    private boolean drain(PacketSource source) {
        IPv4Packet packet;
        while ((packet = source.get()) != null) {
            if (!sendToClient(packet)) {
                return false;
            }
            source.next();
        }
        return true;
    }

    /**
     * Forget a packet source (typically a closed connection), so that it is not pulled anymore.
     *
     * @param source the packet source
     */
    public void removePendingPacketSource(Object source) {
        pendingPacketSources.remove(source);
    }

    private void processPending() {
        Iterator<PacketSource> iterator = pendingPacketSources.iterator();
        while (iterator.hasNext()) {
            PacketSource packetSource = iterator.next();
            if (drain(packetSource)) {
                Log.d(TAG, "Pending packets sent to client");
                iterator.remove();
            } else {
                if (isAttached()) {
                    Log.w(TAG, "Pending packets not all sent to client, client buffer full again");
                }
                return;
            }
        }
    }

    public void cleanExpiredConnections() {
        router.cleanExpiredConnections();
    }
}
