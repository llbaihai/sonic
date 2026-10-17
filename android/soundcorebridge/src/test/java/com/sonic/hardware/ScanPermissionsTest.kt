package com.sonic.hardware

import org.junit.Assert.*
import org.junit.Test

class ScanPermissionsTest {
    @Test fun nearbyPermissionAloneDoesNotAllowVendorScanOnAndroid12() {
        val granted = setOf("android.permission.BLUETOOTH_SCAN", "android.permission.BLUETOOTH_CONNECT")
        val missing = requiredScanPermissions(31).filterNot { it in granted }
        assertTrue("SDK needs precise location even when Nearby devices is allowed",
            "android.permission.ACCESS_FINE_LOCATION" in missing)
        assertTrue("Android 12 requires coarse and fine location requested together",
            "android.permission.ACCESS_COARSE_LOCATION" in missing)
    }

    @Test fun android11DoesNotRequestAndroid12BluetoothPermissions() {
        val requested = requiredScanPermissions(30)
        assertTrue("android.permission.ACCESS_FINE_LOCATION" in requested)
        assertFalse("android.permission.BLUETOOTH_SCAN" in requested)
        assertFalse("android.permission.BLUETOOTH_CONNECT" in requested)
    }
}
