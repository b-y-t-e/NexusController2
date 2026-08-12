package com.nexuscontroller.pad

import android.content.Context
import android.net.wifi.WifiManager
import android.os.Build
import android.util.Log

/**
 * Keeps the Wi-Fi radio awake and in its low-latency mode while a pad is connected.
 *
 * Android puts the radio into a power-saving doze between packets. A phone that
 * sends a few hundred bytes a second looks idle to that logic, so the radio sleeps
 * and the *next* packet waits for it to wake — which is why the delay on a pad is
 * usually not constant but comes in spikes of tens of milliseconds.
 *
 * [WifiManager.WIFI_MODE_FULL_LOW_LATENCY] exists for exactly this: real-time
 * traffic that is small, frequent and worth spending battery on. It only applies
 * while the screen is on and the app is in the foreground, which is precisely
 * when someone is holding the pad.
 *
 * The lock is released the moment the session ends — it costs battery, and a pad
 * nobody is using has no claim on the radio.
 */
class LowLatencyWifi(context: Context) {

    private val wifi = context.applicationContext
        .getSystemService(Context.WIFI_SERVICE) as? WifiManager

    private val lock: WifiManager.WifiLock? = try {
        wifi?.createWifiLock(
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                WifiManager.WIFI_MODE_FULL_LOW_LATENCY
            } else {
                // Deprecated on newer releases but the only option before Q, and
                // still the difference between a dozing radio and an awake one.
                @Suppress("DEPRECATION")
                WifiManager.WIFI_MODE_FULL_HIGH_PERF
            },
            "NexusController:pad"
        )
    } catch (e: Exception) {
        Log.w(TAG, "no Wi-Fi lock available", e)
        null
    }

    fun acquire() {
        val held = lock ?: return
        if (!held.isHeld) {
            runCatching { held.acquire() }
                .onFailure { Log.w(TAG, "could not take the Wi-Fi lock", it) }
        }
    }

    fun release() {
        val held = lock ?: return
        if (held.isHeld) {
            runCatching { held.release() }
                .onFailure { Log.w(TAG, "could not release the Wi-Fi lock", it) }
        }
    }

    private companion object {
        const val TAG = "Nexus"
    }
}
