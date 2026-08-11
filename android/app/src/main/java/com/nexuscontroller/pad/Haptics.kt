package com.nexuscontroller.pad

import android.content.Context
import android.os.Build
import android.os.VibrationAttributes
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log

/**
 * Thin wrapper over [Vibrator]. The system service is resolved once, not on every button
 * press (the pad emits up to 66 input frames per second).
 */
class Haptics(context: Context) {

    private val vibrator: Vibrator? = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            (context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }
    } catch (e: Exception) {
        Log.w(TAG, "no vibrator available", e)
        null
    }

    private val available: Boolean = vibrator?.hasVibrator() == true

    /** Short touch feedback. Works offline — it is local feedback, not server rumble. */
    fun tap(enabled: Boolean, strength: Float) {
        if (!enabled) return
        val amplitude = (255 * strength).toInt().coerceIn(1, 255)
        play(40, amplitude)
    }

    /** Rumble requested by the PC, `0..255`. */
    fun rumble(strength: Int, scale: Float) {
        if (strength <= 0) {
            cancel()
            return
        }
        play(200, (strength * scale).toInt().coerceIn(1, 255))
    }

    fun cancel() {
        if (!available) return
        try {
            vibrator?.cancel()
        } catch (e: Exception) {
            Log.w(TAG, "vibrator cancel failed", e)
        }
    }

    private fun play(durationMs: Long, amplitude: Int) {
        val v = vibrator ?: return
        if (!available) return
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                // USAGE_MEDIA so it still fires when system touch feedback is off.
                val attrs = VibrationAttributes.Builder().setUsage(VibrationAttributes.USAGE_MEDIA).build()
                v.vibrate(VibrationEffect.createOneShot(durationMs, amplitude), attrs)
            } else {
                v.vibrate(VibrationEffect.createOneShot(durationMs, amplitude))
            }
        } catch (e: Exception) {
            Log.w(TAG, "vibrate failed", e)
        }
    }

    private companion object {
        const val TAG = "Nexus"
    }
}
