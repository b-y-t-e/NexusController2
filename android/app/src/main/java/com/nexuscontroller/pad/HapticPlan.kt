package com.nexuscontroller.pad

/**
 * How to ask a particular phone to buzz — decided without touching Android.
 *
 * Three devices where "nothing happens" turned out to have three different
 * causes, and none of them was an exception anybody could see in a log:
 *
 * * **a tablet has no vibration motor at all.** Most do not. The app kept a
 *   switch that said "on" and did nothing for ever.
 * * **many Huawei phones have no amplitude control.** `createOneShot(ms, 216)`
 *   on that hardware is documented to fall back to the default strength, and on
 *   several EMUI builds simply produces nothing. Asking for
 *   [DEFAULT_AMPLITUDE] instead is the same request, phrased the way the device
 *   understands, and where the platform offers a *predefined* click — the effect
 *   OEM haptics engines actually implement — a short tap uses that.
 * * **an un-attributed vibration is touch feedback**, and Android suppresses
 *   touch feedback when the user has turned it off in system settings, which is
 *   a different setting from the one in this app. That is why this attaches a
 *   usage on every version that has one: it is game feedback, not a keyboard
 *   click, and the person holding the pad has already said they want it.
 */
sealed interface HapticPlan {
    /** Nothing to do: this device cannot vibrate. */
    data object None : HapticPlan

    /** ``VibrationEffect.createPredefined`` — the OEM's own click. */
    data class Predefined(val effect: Int) : HapticPlan

    /** ``VibrationEffect.createOneShot``; [amplitude] may be [DEFAULT_AMPLITUDE]. */
    data class OneShot(val durationMs: Long, val amplitude: Int) : HapticPlan

    /** Pre-Oreo ``vibrate(long)``: length is the only dial there is. */
    data class Legacy(val durationMs: Long) : HapticPlan

    companion object {
        /** ``VibrationEffect.DEFAULT_AMPLITUDE``, spelled here so this file needs no Android. */
        const val DEFAULT_AMPLITUDE = -1

        /** ``VibrationEffect.EFFECT_CLICK``, likewise. */
        const val EFFECT_CLICK = 0

        /** Below this a buzz is a tap; above it, a rumble the PC asked for. */
        const val TAP_CEILING_MS = 60L

        /**
         * @param sdkInt          this phone's API level
         * @param hasVibrator     what the system service says it has
         * @param amplitudeControl whether it can be asked for a *strength*
         * @param canPredefine    whether ``createPredefined`` exists (API 29+)
         */
        fun of(
            sdkInt: Int,
            hasVibrator: Boolean,
            amplitudeControl: Boolean,
            canPredefine: Boolean,
            durationMs: Long,
            amplitude: Int,
        ): HapticPlan = when {
            !hasVibrator -> None
            sdkInt < 26 -> Legacy(durationMs)
            // A tap on hardware that cannot be asked for a strength: the OEM's
            // own click is tuned for that motor and is the one thing on such a
            // device that reliably fires.
            !amplitudeControl && canPredefine && durationMs <= TAP_CEILING_MS ->
                Predefined(EFFECT_CLICK)
            // Anything else it cannot scale is asked for at its own default,
            // never at a number it is going to ignore or read as silence.
            !amplitudeControl -> OneShot(durationMs, DEFAULT_AMPLITUDE)
            else -> OneShot(durationMs, amplitude.coerceIn(1, 255))
        }
    }
}
