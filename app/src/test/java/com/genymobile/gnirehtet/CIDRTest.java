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

import org.junit.Assert;
import org.junit.Test;

@SuppressWarnings("checkstyle:MagicNumber")
public class CIDRTest {

    @Test
    public void testParseValid() throws InvalidCIDRException {
        CIDR cidr = CIDR.parse("192.168.1.0/24");
        Assert.assertEquals("192.168.1.0", cidr.getAddress().getHostAddress());
        Assert.assertEquals(24, cidr.getPrefixLength());

        cidr = CIDR.parse("10.0.0.1");
        Assert.assertEquals(32, cidr.getPrefixLength());

        Assert.assertEquals(0, CIDR.parse("0.0.0.0/0").getPrefixLength());
    }

    @Test(expected = InvalidCIDRException.class)
    public void testPrefixTooLarge() throws InvalidCIDRException {
        CIDR.parse("192.168.1.0/33");
    }

    @Test(expected = InvalidCIDRException.class)
    public void testNegativePrefix() throws InvalidCIDRException {
        CIDR.parse("192.168.1.0/-1");
    }

    @Test(expected = InvalidCIDRException.class)
    public void testHostNameRejected() throws InvalidCIDRException {
        // must never trigger a DNS lookup
        CIDR.parse("example.com/24");
    }
}
