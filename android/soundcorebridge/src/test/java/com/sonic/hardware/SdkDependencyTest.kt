package com.sonic.hardware

import org.junit.Test
import org.junit.Assert.assertNotNull

/** Exercise the vendor's actual authentication client construction without network I/O. */
class SdkDependencyTest {
    @Test fun vendorAuthenticationClientCanBeConstructed() {
        val client = com.oceanwing.soundcore.spplink.service.http.KtorHttpClientFactory.createHttpClientForAuth()
        assertNotNull(client)
        client.close()
    }
}
