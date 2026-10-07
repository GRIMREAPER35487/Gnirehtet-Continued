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

import android.net.LocalSocket;
import android.net.LocalSocketAddress;
import android.net.VpnService;
import android.util.Log;

import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;

public final class RelayTunnel implements Tunnel {

    private static final String TAG = RelayTunnel.class.getSimpleName();

    private static final String LOCAL_ABSTRACT_NAME = "gnirehtet";
    private static final int BUFFER_SIZE = 2 * 1024 * 1024;
    private static final int CONNECT_TIMEOUT_MS = 5000;

    private final VpnService vpnService;
    private final String relayHost;
    private final int relayPort;

    private LocalSocket localSocket;
    private Socket tcpSocket;
    private InputStream inputStream;
    private OutputStream outputStream;

    private RelayTunnel(VpnService vpnService, String relayHost, int relayPort) {
        this.vpnService = vpnService;
        this.relayHost = relayHost;
        this.relayPort = relayPort;
    }

    public static RelayTunnel open(VpnService vpnService) throws IOException {
        return open(vpnService, null, 0);
    }

    public static RelayTunnel open(VpnService vpnService, String relayHost, int relayPort) throws IOException {
        Log.d(TAG, "Opening a new relay tunnel (host=" + relayHost + ", port=" + relayPort + ")...");
        return new RelayTunnel(vpnService, relayHost, relayPort);
    }

    public void connect() throws IOException {
        if (relayHost != null && !relayHost.isEmpty()) {
            Log.i(TAG, "Connecting to Native USB relay at " + relayHost + ":" + relayPort);
            tcpSocket = new Socket();
            if (vpnService != null) {
                vpnService.protect(tcpSocket);
            }
            tcpSocket.connect(new InetSocketAddress(relayHost, relayPort), CONNECT_TIMEOUT_MS);
            tcpSocket.setTcpNoDelay(true);
            tcpSocket.setReceiveBufferSize(BUFFER_SIZE);
            tcpSocket.setSendBufferSize(BUFFER_SIZE);
            inputStream = tcpSocket.getInputStream();
            outputStream = tcpSocket.getOutputStream();
        } else {
            Log.i(TAG, "Connecting to legacy ADB reverse socket: " + LOCAL_ABSTRACT_NAME);
            localSocket = new LocalSocket();
            localSocket.connect(new LocalSocketAddress(LOCAL_ABSTRACT_NAME));
            localSocket.setReceiveBufferSize(BUFFER_SIZE);
            localSocket.setSendBufferSize(BUFFER_SIZE);
            inputStream = localSocket.getInputStream();
            outputStream = localSocket.getOutputStream();
        }
        readClientId(inputStream);
    }

    /**
     * The relay server sends the client id immediately upon connection.
     *
     * @param in the input stream to receive data from the relay server
     * @throws IOException if an I/O error occurs
     */
    private static void readClientId(InputStream in) throws IOException {
        Log.d(TAG, "Requesting client id");
        int clientId = new DataInputStream(in).readInt();
        Log.d(TAG, "Connected to the relay server as #" + Binary.unsigned(clientId));
    }

    @Override
    public void send(byte[] packet, int len) throws IOException {
        if (GnirehtetService.VERBOSE) {
            Log.v(TAG, "Sending packet: " + Binary.buildPacketString(packet, len));
        }
        outputStream.write(packet, 0, len);
    }

    @Override
    public int receiveTo(byte[] buffer, int offset, int maxLen) throws IOException {
        int r = inputStream.read(buffer, offset, maxLen);
        if (GnirehtetService.VERBOSE) {
            Log.v(TAG, "Receiving packet: " + r + " bytes");
        }
        return r;
    }

    @Override
    public void close() {
        try {
            if (tcpSocket != null) {
                tcpSocket.close();
                tcpSocket = null;
            }
            if (localSocket != null) {
                if (localSocket.getFileDescriptor() != null) {
                    localSocket.shutdownInput();
                    localSocket.shutdownOutput();
                }
                localSocket.close();
                localSocket = null;
            }
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }
}
