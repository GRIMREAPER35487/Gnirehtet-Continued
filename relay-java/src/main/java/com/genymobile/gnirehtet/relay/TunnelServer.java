package com.genymobile.gnirehtet.relay;

import java.io.IOException;
import java.net.Inet4Address;
import java.net.InetSocketAddress;
import java.net.StandardSocketOptions;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.util.ArrayList;
import java.util.List;

/**
 * Handle the connections from the clients.
 */
public class TunnelServer {

    private static final String TAG = TunnelServer.class.getSimpleName();

    private final List<Client> clients = new ArrayList<>();
    private final Relay.RelayListener listener;

    public TunnelServer(int port, Selector selector) throws IOException {
        this(port, selector, null);
    }

    public TunnelServer(int port, Selector selector, Relay.RelayListener listener) throws IOException {
        this.listener = listener;
        ServerSocketChannel serverSocketChannel = ServerSocketChannel.open();
        serverSocketChannel.configureBlocking(false);
        // ServerSocketChannel.bind() requires API 24
        serverSocketChannel.socket().bind(new InetSocketAddress(Inet4Address.getLoopbackAddress(), port));

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
        socketChannel.configureBlocking(false);
        socketChannel.setOption(StandardSocketOptions.TCP_NODELAY, true);
        socketChannel.setOption(StandardSocketOptions.SO_SNDBUF, 2 * 1024 * 1024);
        socketChannel.setOption(StandardSocketOptions.SO_RCVBUF, 2 * 1024 * 1024);
        // will register the socket on the selector
        Client client = new Client(selector, socketChannel, this::removeClient);
        clients.add(client);
        Log.i(TAG, "Client #" + client.getId() + " connected");
    }

    private void removeClient(Client client) {
        clients.remove(client);
        Log.i(TAG, "Client #" + client.getId() + " disconnected");
        if (listener != null) {
            listener.onClientDisconnected();
        }
    }

    public void cleanUp() {
        for (Client client : clients) {
            client.cleanExpiredConnections();
        }
    }
}
