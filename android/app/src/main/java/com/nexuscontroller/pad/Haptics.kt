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
 * press (a pad under a fast thumb emits a couple of hundred frames a second).
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

    /**
     * Three branches, because three different APIs are involved and getting the
     * boundary wrong is a crash rather than a missing buzz: a class that does not
     * exist yet throws [NoClassDefFoundError], which is an `Error` and so sails
     * straight through `catch (e: Exception)`.
     *
     * * [VibrationAttributes] arrived in **API 33**, not 31 — guarding it with
     *   `S` killed the app on every Android 12 and 12L phone.
     * * [VibrationEffect] arrived in **API 26**, and the legacy flavour installs
     *   back to 21, where the only vibrate() that exists takes a duration.
     */
    // ObsoleteSdkInt: true of the `modern` flavour, whose minSdk is 28, and false
    // of `legacy`, which starts at 21 — one source, two minimums, and lint judges
    // each build on its own. The branch below is what keeps the app alive on
    // Android 5 to 7; the warning must not be the reason somebody deletes it.
    @Suppress("ObsoleteSdkInt")
    private fun play(durationMs: Long, amplitude: Int) {
        val v = vibrator ?: return
        if (!available) return
        try {
            when {
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU -> {
                    // USAGE_MEDIA so it still fires when system touch feedback is off.
                    val attrs = VibrationAttributes.Builder()
                        .setUsage(VibrationAttributes.USAGE_MEDIA)
                        .build()
                    v.vibrate(VibrationEffect.createOneShot(durationMs, amplitude), attrs)
                }
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.O ->
                    v.vibrate(VibrationEffect.createOneShot(durationMs, amplitude))
                else -> {
                    // No amplitude control before Oreo: the phone buzzes at whatever
                    // strength it has, for as long as we ask. Length is the only
                    // dial left, so the strength setting simply has less to say.
                    @Suppress("DEPRECATION")
                    v.vibrate(durationMs)
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "vibrate failed", e)
        }
    }

    private companion object {
        const val TAG = "Nexus"
    }
}
