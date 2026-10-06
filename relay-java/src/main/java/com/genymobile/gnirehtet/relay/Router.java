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
import java.nio.channels.Selector;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

public class Router {

    private static final String TAG = Router.class.getSimpleName();

    private final Client client;
    private final Selector selector;

    private final Map<ConnectionId, Connection> connections = new HashMap<>();

    public Router(Client client, Selector selector) {
        this.client = client;
        this.selector = selector;
    }

    public void sendToNetwork(IPv4Packet packet) {
        if (!packet.isValid()) {
            Log.w(TAG, "Dropping invalid packet");
            if (Log.isVerboseEnabled()) {
                Log.v(TAG, Binary.buildPacketString(packet.getRaw()));
            }
            return;
        }
        try {
            Connection connection = getConnection(packet.getIpv4Header(), packet.getTransportHeader());
            connection.sendToNetwork(packet);
        } catch (IOException e) {
            Log.e(TAG, "Cannot create connection, dropping packet", e);
        }
    }

    private Connection getConnection(IPv4Header ipv4Header, TransportHeader transportHeader) throws IOException {
        ConnectionId id = ConnectionId.from(ipv4Header, transportHeader);
        Connection connection = find(id);
        if (connection == null) {
            connection = createConnection(id, ipv4Header, transportHeader);
            connections.put(id, connection);
        }
        return connection;
    }

    private Connection createConnection(ConnectionId id, IPv4Header ipv4Header, TransportHeader transportHeader) throws IOException {
        IPv4Header.Protocol protocol = id.getProtocol();
        if (protocol == IPv4Header.Protocol.UDP) {
            return new UDPConnection(id, client, selector, ipv4Header, (UDPHeader) transportHeader);
        }
        if (protocol == IPv4Header.Protocol.TCP) {
            return new TCPConnection(id, client, selector, ipv4Header, (TCPHeader) transportHeader);
        }
        throw new UnsupportedOperationException("Unsupported protocol: " + protocol);
    }

    private Connection find(ConnectionId id) {
        return connections.get(id);
    }

    public void clear() {
        for (Connection connection : connections.values()) {
            connection.disconnect();
        }
        connections.clear();
    }

    public void remove(Connection connection) {
        if (connections.remove(connection.getId()) == null) {
            throw new AssertionError("Removed a connection unknown from the router");
        }
    }

    public void cleanExpiredConnections() {
        Iterator<Connection> iterator = connections.values().iterator();
        while (iterator.hasNext()) {
            Connection connection = iterator.next();
            if (connection.isExpired()) {
                if (Log.isDebugEnabled()) {
                    Log.d(TAG, "Remove expired connection: " + connection.getId());
                }
                connection.disconnect();
                iterator.remove();
            }
        }
    }
}
