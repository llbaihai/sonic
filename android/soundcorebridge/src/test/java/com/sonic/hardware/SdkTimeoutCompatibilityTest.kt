package com.sonic.hardware

import com.oceanwing.soundcore.spplink.timeout.TimeoutManager
import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Test

class SdkTimeoutCompatibilityTest {
    @Test fun receivedTimeSyncCanCancelPendingVendorTimeout() = runBlocking {
        val manager = TimeoutManager()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        try {
            manager.registerTimeout("sync-time", 60000L, scope) { fail("Cancelled timeout fired") }
            assertEquals(1, manager.getActiveTimeoutCount())
            assertTrue(manager.cancelTimeout("sync-time"))
            assertEquals(0, manager.getActiveTimeoutCount())
            assertFalse(manager.cancelTimeout("sync-time"))
        } finally { scope.cancel() }
    }

    @Test fun disconnectCanCancelAllPendingVendorTimeouts() = runBlocking {
        val manager = TimeoutManager()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        try {
            manager.registerTimeout("sync-time", 60000L, scope) { fail("Cancelled timeout fired") }
            manager.registerTimeout("device-info", 60000L, scope) { fail("Cancelled timeout fired") }
            manager.cancelAllTimeouts()
            assertEquals(0, manager.getActiveTimeoutCount())
        } finally { scope.cancel() }
    }
}
