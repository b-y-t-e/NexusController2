package com.nexuscontroller.pad

import android.content.SharedPreferences
import androidx.compose.runtime.snapshots.SnapshotStateMap

/**
 * Reads and writes on-screen layouts in the `layout_prefs` SharedPreferences.
 *
 * Layouts are keyed per profile *and* per controller family (see
 * [LayoutSerializer.prefsKey]) so switching to Buzz and back cannot scramble a saved
 * gamepad layout. The serialisation itself lives in [LayoutSerializer], which is pure
 * Kotlin and unit-tested.
 */
class LayoutStore(
    private val prefs: SharedPreferences,
    private val profilesPrefs: SharedPreferences
) {

    companion object {
        const val DEFAULT_PROFILE = "autosave"

        /** Component IDs of the gamepad layout. */
        val GAMEPAD_IDS = listOf(
            "L1", "L2", "R1", "R2", "SHARE", "OPTIONS", "PS", "L_STICK", "DPAD", "FACE", "R_STICK"
        )

        /** Component IDs of the Buzz layout — namespaced so they never collide. */
        val BUZZ_IDS = listOf("BUZZ_RED", "BUZZ_BLUE", "BUZZ_ORANGE", "BUZZ_GREEN", "BUZZ_YELLOW")

        fun defaults(type: ControllerType, w: Float, h: Float): Map<String, LayoutEntry> =
            if (type.isGamepad) gamepadDefaults(w, h) else buzzDefaults(w, h)

        private fun gamepadDefaults(w: Float, h: Float) = mapOf(
            "L1" to LayoutEntry(w * 0.08f, h * 0.12f, 0.9f),
            "L2" to LayoutEntry(w * 0.08f, h * 0.25f, 0.9f),
            "R1" to LayoutEntry(w * 0.82f, h * 0.12f, 0.9f),
            "R2" to LayoutEntry(w * 0.82f, h * 0.25f, 0.9f),
            "SHARE" to LayoutEntry(w * 0.38f, h * 0.28f, 0.9f),
            "OPTIONS" to LayoutEntry(w * 0.58f, h * 0.28f, 0.9f),
            "PS" to LayoutEntry(w * 0.46f, h * 0.32f, 1.0f),
            "L_STICK" to LayoutEntry(w * 0.08f, h * 0.65f, 1.2f),
            "DPAD" to LayoutEntry(w * 0.30f, h * 0.60f, 1.0f),
            "FACE" to LayoutEntry(w * 0.55f, h * 0.60f, 1.0f),
            "R_STICK" to LayoutEntry(w * 0.80f, h * 0.65f, 1.2f)
        )

        /** Big dome centred near the top, four answer buttons in a row underneath. */
        private fun buzzDefaults(w: Float, h: Float) = mapOf(
            "BUZZ_RED" to LayoutEntry(w * 0.5f - 190f, h * 0.10f, 1.0f),
            "BUZZ_BLUE" to LayoutEntry(w * 0.5f - 320f, h * 0.62f, 1.0f),
            "BUZZ_ORANGE" to LayoutEntry(w * 0.5f - 130f, h * 0.62f, 1.0f),
            "BUZZ_GREEN" to LayoutEntry(w * 0.5f + 60f, h * 0.62f, 1.0f),
            "BUZZ_YELLOW" to LayoutEntry(w * 0.5f + 250f, h * 0.62f, 1.0f)
        )
    }

    fun profileList(): List<String> =
        profilesPrefs.getStringSet("names", emptySet())?.toList()?.sorted() ?: emptyList()

    fun activeProfile(): String =
        prefs.getString("active_profile", DEFAULT_PROFILE) ?: DEFAULT_PROFILE

    fun setActiveProfile(name: String) {
        prefs.edit().putString("active_profile", name).apply()
    }

    fun controllerType(): ControllerType =
        ControllerType.fromStorage(prefs.getString("controller_type", ControllerType.XBOX360.name))

    fun setControllerType(type: ControllerType) {
        prefs.edit().putString("controller_type", type.name).apply()
    }

    fun save(configs: Map<String, CompConfig>, profile: String, type: ControllerType) {
        val json = LayoutSerializer.encode(configs.mapValues { it.value.toEntry() })
        prefs.edit().putString(LayoutSerializer.prefsKey(profile, type), json).apply()
        if (profile != DEFAULT_PROFILE) {
            val set = profilesPrefs.getStringSet("names", emptySet())?.toMutableSet() ?: mutableSetOf()
            set.add(profile)
            profilesPrefs.edit().putStringSet("names", set).apply()
        }
    }

    /**
     * Loads into [target]. Nothing stored yet means the caller gets an empty map and the
     * screen seeds the size-dependent defaults once it knows its dimensions.
     */
    fun load(target: SnapshotStateMap<String, CompConfig>, profile: String, type: ControllerType) {
        val key = LayoutSerializer.prefsKey(profile, type)
        // Legacy migration: v1 stored the autosave gamepad layout under a bare key.
        if (type.isGamepad && profile == DEFAULT_PROFILE && !prefs.contains(key) && prefs.contains("layout_json")) {
            prefs.edit().putString(key, prefs.getString("layout_json", null)).apply()
        }
        val stored = LayoutSerializer.decode(prefs.getString(key, null))
        target.clear()
        stored.forEach { (id, entry) -> target[id] = CompConfig.from(entry) }
    }

    fun renameProfile(oldName: String, newName: String): Boolean {
        val set = profilesPrefs.getStringSet("names", emptySet())?.toMutableSet() ?: return false
        if (!set.contains(oldName) || set.contains(newName)) return false
        set.remove(oldName)
        set.add(newName)
        profilesPrefs.edit().putStringSet("names", set).apply()
        val editor = prefs.edit()
        ControllerType.entries.forEach { type ->
            val from = LayoutSerializer.prefsKey(oldName, type)
            if (prefs.contains(from)) {
                editor.putString(LayoutSerializer.prefsKey(newName, type), prefs.getString(from, null))
                editor.remove(from)
            }
        }
        editor.apply()
        return true
    }

    fun deleteProfile(name: String) {
        if (name == DEFAULT_PROFILE) return
        val set = profilesPrefs.getStringSet("names", emptySet())?.toMutableSet() ?: return
        if (!set.remove(name)) return
        profilesPrefs.edit().putStringSet("names", set).apply()
        val editor = prefs.edit()
        ControllerType.entries.forEach { editor.remove(LayoutSerializer.prefsKey(name, it)) }
        editor.apply()
    }
}
