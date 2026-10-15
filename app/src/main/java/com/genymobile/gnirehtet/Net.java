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

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.UnknownHostException;
import java.util.regex.Pattern;

public final class Net {

    private static final String IPV4_BYTE = "(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])";
    private static final Pattern IPV4_LITERAL = Pattern.compile(IPV4_BYTE + "(\\." + IPV4_BYTE + "){3}");

    private Net() {
        // not instantiable
    }

    public static InetAddress[] toInetAddresses(String... addresses) {
        InetAddress[] result = new InetAddress[addresses.length];
        for (int i = 0; i < result.length; ++i) {
            result[i] = toInetAddress(addresses[i]);
        }
        return result;
    }

    public static InetAddress toInetAddress(String address) {
        try {
            return InetAddress.getByName(address);
        } catch (UnknownHostException e) {
            throw new IllegalArgumentException(e);
        }
    }

    /**
     * Parse a literal IPv4 address (never resolves a host name).
     *
     * @param address the dotted-decimal IPv4 address
     * @return the address
     * @throws IllegalArgumentException if {@code address} is not a literal IPv4 address
     */
    public static InetAddress toIPv4LiteralAddress(String address) {
        if (!IPV4_LITERAL.matcher(address).matches()) {
            throw new IllegalArgumentException("Not a literal IPv4 address: " + address);
        }
        // a literal address is parsed without any DNS lookup
        return toInetAddress(address);
    }

    public static InetAddress toInetAddress(byte[] raw) {
        try {
            return InetAddress.getByAddress(raw);
        } catch (UnknownHostException e) {
            throw new IllegalArgumentException(e);
        }
    }

    public static CIDR toCIDR(String cidr) {
        try {
            return CIDR.parse(cidr);
        } catch (InvalidCIDRException e) {
            throw new IllegalArgumentException(e);
        }
    }

    public static CIDR[] toCIDRs(String... cidrs) {
        CIDR[] result = new CIDR[cidrs.length];
        for (int i = 0; i < result.length; ++i) {
            result[i] = toCIDR(cidrs[i]);
        }
        return result;
    }

    @SuppressWarnings("checkstyle:MagicNumber")
    public static Inet4Address getLocalhostIPv4() {
        byte[] localhost = {127, 0, 0, 1};
        return (Inet4Address) toInetAddress(localhost);
    }
}
