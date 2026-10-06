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

public record ConnectionId(
    IPv4Header.Protocol protocol,
    int sourceIp,
    short sourcePort,
    int destIp,
    short destPort
) {
    public IPv4Header.Protocol getProtocol() {
        return protocol;
    }

    public int getSourceIp() {
        return sourceIp;
    }

    public int getSourcePort() {
        return Short.toUnsignedInt(sourcePort);
    }

    public int getDestinationIp() {
        return destIp;
    }

    public int getDestinationPort() {
        return Short.toUnsignedInt(destPort);
    }

    @Override
    public String toString() {
        return protocol + " " + Net.toString(sourceIp, sourcePort) + " -> " + Net.toString(destIp, destPort);
    }

    public static ConnectionId from(IPv4Header ipv4Header, TransportHeader transportHeader) {
        IPv4Header.Protocol protocol = ipv4Header.getProtocol();
        int sourceAddress = ipv4Header.getSource();
        short sourcePort = (short) transportHeader.getSourcePort();
        int destinationAddress = ipv4Header.getDestination();
        short destinationPort = (short) transportHeader.getDestinationPort();
        return new ConnectionId(protocol, sourceAddress, sourcePort, destinationAddress, destinationPort);
    }
}
