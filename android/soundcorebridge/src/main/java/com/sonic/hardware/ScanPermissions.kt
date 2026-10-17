package com.sonic.hardware

import android.Manifest

/** Permissions requested before calling the vendor scanner. */
fun requiredScanPermissions(apiLevel: Int): List<String> =
    if (apiLevel >= 31) listOf(
        Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT,
        Manifest.permission.ACCESS_COARSE_LOCATION, Manifest.permission.ACCESS_FINE_LOCATION)
    else listOf(Manifest.permission.ACCESS_FINE_LOCATION)
