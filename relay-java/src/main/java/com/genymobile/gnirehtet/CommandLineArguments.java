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

import java.util.regex.Pattern;

/**
 * Simple specific command-line arguments parser.
 */
@SuppressWarnings("checkstyle:MagicNumber")
public class CommandLineArguments {

    public static final int PARAM_NONE = 0;
    public static final int PARAM_SERIAL = 1;
    public static final int PARAM_DNS_SERVER = 1 << 1;
    public static final int PARAM_ROUTES = 1 << 2;
    public static final int PARAM_PORT = 1 << 3;

    public static final int DEFAULT_PORT = 31416;

    private static final String IPV4_BYTE = "(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])";
    private static final String IPV4 = IPV4_BYTE + "(\\." + IPV4_BYTE + "){3}";
    private static final Pattern IPV4_LIST = Pattern.compile(IPV4 + "(," + IPV4 + ")*");
    private static final String CIDR = IPV4 + "(/(3[0-2]|[12]?[0-9]))?";
    private static final Pattern CIDR_LIST = Pattern.compile(CIDR + "(," + CIDR + ")*");

    private int port;
    private String serial;
    private String dnsServers;
    private String routes;

    public static CommandLineArguments parse(int acceptedParameters, String... args) {
        CommandLineArguments arguments = new CommandLineArguments();
        for (int i = 0; i < args.length; ++i) {
            String arg = args[i];
            if ((acceptedParameters & PARAM_DNS_SERVER) != 0 && "-d".equals(arg)) {
                if (arguments.dnsServers != null) {
                    throw new IllegalArgumentException("DNS servers already set");
                }
                arguments.dnsServers = getOptionValue(args, i, "-d");
                ++i; // consume the -d parameter
            } else if ((acceptedParameters & PARAM_ROUTES) != 0 && "-r".equals(arg)) {
                if (arguments.routes != null) {
                    throw new IllegalArgumentException("Routes already set");
                }
                arguments.routes = getOptionValue(args, i, "-r");
                ++i; // consume the -r parameter
            } else if ((acceptedParameters & PARAM_PORT) != 0 && "-p".equals(arg)) {
                if (arguments.port != 0) {
                    throw new IllegalArgumentException("Port already set");
                }
                arguments.port = Integer.parseInt(getOptionValue(args, i, "-p"));
                if (arguments.port <= 0 || arguments.port >= 65536) {
                    throw new IllegalArgumentException("Invalid port: " + arguments.port);
                }
                ++i;
            } else if ((acceptedParameters & PARAM_SERIAL) != 0 && arguments.serial == null) {
                arguments.serial = arg;
            } else {
                throw new IllegalArgumentException("Unexpected argument: \"" + arg + "\"");
            }
        }
        if (arguments.port == 0) {
            arguments.port = DEFAULT_PORT;
        }
        return arguments;
    }

    private static String getOptionValue(String[] args, int optionIndex, String option) {
        if (optionIndex == args.length - 1 || args[optionIndex + 1].startsWith("-")) {
            // a value starting with '-' is another option, the value is missing
            throw new IllegalArgumentException("Missing " + option + " parameter");
        }
        return args[optionIndex + 1];
    }

    /**
     * Check that {@code dnsServers} is a comma-separated list of IPv4 addresses.
     * <p>
     * The value is passed through "adb shell", so it must not contain any shell metacharacter.
     *
     * @param dnsServers the DNS servers
     * @throws IllegalArgumentException if the value is invalid
     */
    public static void validateDnsServers(String dnsServers) {
        if (dnsServers != null && !IPV4_LIST.matcher(dnsServers).matches()) {
            throw new IllegalArgumentException("Invalid DNS servers (expected comma-separated IPv4 addresses): " + dnsServers);
        }
    }

    /**
     * Check that {@code routes} is a comma-separated list of IPv4 CIDR.
     * <p>
     * The value is passed through "adb shell", so it must not contain any shell metacharacter.
     *
     * @param routes the routes
     * @throws IllegalArgumentException if the value is invalid
     */
    public static void validateRoutes(String routes) {
        if (routes != null && !CIDR_LIST.matcher(routes).matches()) {
            throw new IllegalArgumentException("Invalid routes (expected comma-separated IPv4 CIDR): " + routes);
        }
    }

    public String getSerial() {
        return serial;
    }

    public String getDnsServers() {
        return dnsServers;
    }

    public String getRoutes() {
        return routes;
    }

    public int getPort() {
        return port;
    }
}
