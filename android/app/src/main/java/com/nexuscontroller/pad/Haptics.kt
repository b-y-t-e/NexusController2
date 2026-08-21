package com.nexuscontroller.pad

import android.content.Context
import android.media.AudioAttributes
import android.os.Build
import android.os.VibrationAttributes
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log
import androidx.annotation.RequiresApi

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

    /**
     * Whether this device can vibrate at all.
     *
     * Public because the settings screen says so: most tablets have no motor,
     * and a switch that stays on while nothing ever happens is worse than a
     * switch that explains itself.
     */
    val hasVibrator: Boolean = vibrator?.hasVibrator() == true

    /**
     * Whether it can be asked for a *strength*. Many Huawei phones cannot, and
     * that is the difference between a tap they feel and one they do not — see
     * [HapticPlan].
     */
    val hasAmplitudeControl: Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            try {
                vibrator?.hasAmplitudeControl() == true
            } catch (e: Exception) {
                Log.w(TAG, "could not ask about amplitude control", e)
                false
            }
        } else {
            false
        }

    private val available: Boolean = hasVibrator

    /** One line for the settings screen and for a bug report. */
    fun describe(): String = when {
        !hasVibrator -> "no vibration motor"
        hasAmplitudeControl -> "strength control"
        else -> "fixed strength"
    }

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
        val plan = HapticPlan.of(
            sdkInt = Build.VERSION.SDK_INT,
            hasVibrator = hasVibrator,
            amplitudeControl = hasAmplitudeControl,
            // createPredefined arrived in API 29.
            canPredefine = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q,
            durationMs = durationMs,
            amplitude = amplitude,
        )
        try {
            when (plan) {
                is HapticPlan.None -> return
                is HapticPlan.Legacy -> {
                    // No amplitude control before Oreo: the phone buzzes at
                    // whatever strength it has, for as long as we ask. Length is
                    // the only dial left, so the strength setting has less to say.
                    @Suppress("DEPRECATION")
                    v.vibrate(plan.durationMs)
                }
                // Every effect-based path behind one version check that lint can
                // see. It cannot follow the flags the plan was built from, and it
                // is right to insist: a class that does not exist yet throws
                // NoClassDefFoundError, an Error, which no catch of Exception
                // will ever see — on a phone in somebody's hand, not here.
                else -> if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    playEffect(v, plan, durationMs, amplitude)
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "vibrate failed", e)
        }
    }

    /** The two effect-based plans, on the versions that have effects at all. */
    @RequiresApi(Build.VERSION_CODES.O)
    private fun playEffect(v: Vibrator, plan: HapticPlan, durationMs: Long, amplitude: Int) {
        val effect = when {
            plan is HapticPlan.Predefined && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ->
                VibrationEffect.createPredefined(plan.effect)
            plan is HapticPlan.OneShot -> VibrationEffect.createOneShot(plan.durationMs, plan.amplitude)
            else -> VibrationEffect.createOneShot(durationMs, amplitude)
        }
        vibrate(v, effect)
    }

    /**
     * Send an effect, saying what it is for.
     *
     * The usage is the point. Without one, Android files a vibration under touch
     * feedback and drops it whenever the user has turned *that* off in system
     * settings — a different switch from the one in this app, and the usual
     * reason a phone "does not vibrate" while everything else about it works.
     * This is game feedback from a pad somebody is holding on purpose, so it
     * says so, on every version that has somewhere to say it.
     */
    @RequiresApi(Build.VERSION_CODES.O)
    private fun vibrate(v: Vibrator, effect: VibrationEffect) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // VibrationAttributes arrived in API 33, not 31: guarding it with S
            // killed the app on every Android 12 and 12L phone, because a class
            // that does not exist yet throws NoClassDefFoundError — an Error, so
            // no catch of Exception ever sees it.
            val attrs = VibrationAttributes.Builder()
                .setUsage(VibrationAttributes.USAGE_MEDIA)
                .build()
            v.vibrate(effect, attrs)
        } else {
            val attrs = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build()
            v.vibrate(effect, attrs)
        }
    }

    private companion object {
        const val TAG = "Nexus"
    }
}
