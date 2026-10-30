package com.genymobile.gnirehtet.relay;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.StandardSocketOptions;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Handle the connections from the clients.
 */
public class TunnelServer {

    private static final String TAG = TunnelServer.class.getSimpleName();

    private final List<Client> clients = new ArrayList<>();
    // clients by session token, to resume a session when its device reconnects
    private final Map<Long, Client> sessions = new HashMap<>();
    private final Relay.RelayListener listener;

    public TunnelServer(int port, Selector selector) throws IOException {
        this(port, selector, null);
    }

    public TunnelServer(int port, Selector selector, Relay.RelayListener listener) throws IOException {
        this.listener = listener;
        ServerSocketChannel serverSocketChannel = ServerSocketChannel.open();
        serverSocketChannel.configureBlocking(false);
        // Bind to all local interfaces (0.0.0.0) so it accepts both direct Native USB (NCM) and legacy ADB reverse loopback
        serverSocketChannel.socket().bind(new InetSocketAddress(port));

        SelectionHandler socketChannelHandler = (selectionKey) -> {
            try {
                ServerSocketChannel channel = (ServerSocketChannel) selectionKey.channel();
                acceptClient(selector, channel);
            } catch (IOException e) {
                Log.e(TAG, "Cannot accept client", e);
            }
        };
        serverSocketChannel.register(selector, SelectionKey.OP_ACCEPT, socketChannelHandler);
    }

    private void acceptClient(Selector selector, ServerSocketChannel serverSocketChannel) throws IOException {
        SocketChannel socketChannel = serverSocketChannel.accept();
        if (socketChannel == null) {
            // spurious wakeup, no pending connection
            return;
        }
        socketChannel.configureBlocking(false);
        socketChannel.setOption(StandardSocketOptions.TCP_NODELAY, true);
        socketChannel.setOption(StandardSocketOptions.SO_SNDBUF, 2 * 1024 * 1024);
        socketChannel.setOption(StandardSocketOptions.SO_RCVBUF, 2 * 1024 * 1024);
        // will register the socket on the selector
        Client client = new Client(selector, socketChannel, this::removeClient, this::onSessionToken);
        clients.add(client);
        Log.i(TAG, "Client #" + client.getId() + " connected");
    }

    private boolean onSessionToken(Client client, long token) {
        Client existing = sessions.get(token);
        if (existing == null || existing == client || client.getRouter().size() != 0) {
            // new session (or a client which already relayed packets: do not mix sessions)
            client.setSessionToken(token);
            sessions.put(token, client);
            return false;
        }
        clients.remove(client);
        existing.resume(client);
        return true;
    }

    private void removeClient(Client client) {
        clients.remove(client);
        long token = client.getSessionToken();
        if (token != 0) {
            sessions.remove(token, client);
        }
        Log.i(TAG, "Client #" + client.getId() + " disconnected");
        if (listener != null) {
            listener.onClientDisconnected();
        }
    }

    public void cleanUp() {
        // iterate over a copy: closing a client removes it from the list
        for (Client client : new ArrayList<>(clients)) {
            if (client.isResumeExpired()) {
                client.close("the device did not reconnect within " + Client.RESUME_TIMEOUT_MS / 1000 + "s");
            } else {
                client.cleanExpiredConnections();
            }
        }
    }
}
