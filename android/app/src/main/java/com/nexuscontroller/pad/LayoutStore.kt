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
 *
 * Everything in and out of here is in the normalised form of PROTOCOL.md §10. [screen] is
 * only needed to migrate layouts written by older builds, which stored raw pixels; it is a
 * `var` because the real window size is not known until the activity has one.
 */
class LayoutStore(
    private val prefs: SharedPreferences,
    private val profilesPrefs: SharedPreferences,
    @Volatile var screen: ScreenSize = ScreenSize.FALLBACK
) {

    companion object {
        const val DEFAULT_PROFILE = "autosave"

        /** Component IDs of the gamepad layout. */
        val GAMEPAD_IDS = listOf(
            "L1", "L2", "R1", "R2", "SHARE", "OPTIONS", "PS", "L_STICK", "DPAD", "FACE", "R_STICK"
        )

        /** Component IDs of the Buzz layout — namespaced so they never collide. */
        val BUZZ_IDS = listOf("BUZZ_RED", "BUZZ_BLUE", "BUZZ_ORANGE", "BUZZ_GREEN", "BUZZ_YELLOW")

        /**
         * Starting placement, as normalised centres. Being screen-independent, these are the
         * same on a 1080p phone and on a tablet — which is the whole point of §10.
         */
        fun defaults(type: ControllerType): Map<String, LayoutEntry> =
            if (type.isGamepad) GAMEPAD_DEFAULTS else BUZZ_DEFAULTS

        private val GAMEPAD_DEFAULTS = linkedMapOf(
            "L1" to LayoutEntry(0.10f, 0.17f, 0.9f),
            "L2" to LayoutEntry(0.10f, 0.34f, 0.9f),
            "R1" to LayoutEntry(0.90f, 0.17f, 0.9f),
            "R2" to LayoutEntry(0.90f, 0.34f, 0.9f),
            "SHARE" to LayoutEntry(0.42f, 0.30f, 0.9f),
            "OPTIONS" to LayoutEntry(0.58f, 0.30f, 0.9f),
            "PS" to LayoutEntry(0.50f, 0.42f, 1.0f),
            "L_STICK" to LayoutEntry(0.15f, 0.70f, 1.2f),
            "DPAD" to LayoutEntry(0.36f, 0.68f, 1.0f),
            "FACE" to LayoutEntry(0.64f, 0.68f, 1.0f),
            "R_STICK" to LayoutEntry(0.85f, 0.70f, 1.2f)
        )

        /** Big dome centred near the top, four answer buttons in a row underneath. */
        private val BUZZ_DEFAULTS = linkedMapOf(
            "BUZZ_RED" to LayoutEntry(0.50f, 0.28f, 1.0f),
            "BUZZ_BLUE" to LayoutEntry(0.20f, 0.75f, 1.0f),
            "BUZZ_ORANGE" to LayoutEntry(0.40f, 0.75f, 1.0f),
            "BUZZ_GREEN" to LayoutEntry(0.60f, 0.75f, 1.0f),
            "BUZZ_YELLOW" to LayoutEntry(0.80f, 0.75f, 1.0f)
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
        saveEntries(configs.mapValues { it.value.toEntry() }, profile, type)
    }

    fun saveEntries(entries: Map<String, LayoutEntry>, profile: String, type: ControllerType) {
        val json = LayoutSerializer.encode(entries)
        prefs.edit().putString(LayoutSerializer.prefsKey(profile, type), json).apply()
        if (profile != DEFAULT_PROFILE) {
            val set = profilesPrefs.getStringSet("names", emptySet())?.toMutableSet() ?: mutableSetOf()
            set.add(profile)
            profilesPrefs.edit().putStringSet("names", set).apply()
        }
    }

    /**
     * Reads a stored layout in normalised form, migrating a legacy pixel layout on the way
     * (see [LayoutMigration]). Nothing stored yet gives an empty map, and the screen seeds
     * [defaults] once it is composed.
     *
     * The migrated values are deliberately *not* written back here: [screen] is only accurate
     * once the window has been measured, so the conversion is redone from the original data
     * until the user's next save persists the normalised form.
     */
    fun loadEntries(profile: String, type: ControllerType): Map<String, LayoutEntry> {
        val key = LayoutSerializer.prefsKey(profile, type)
        // Legacy migration: v1 stored the autosave gamepad layout under a bare key.
        if (type.isGamepad && profile == DEFAULT_PROFILE && !prefs.contains(key) && prefs.contains("layout_json")) {
            prefs.edit().putString(key, prefs.getString("layout_json", null)).apply()
        }
        return LayoutSerializer.decodeNormalised(prefs.getString(key, null), screen)
    }

    /** Loads into [target], replacing whatever it held. */
    fun load(target: SnapshotStateMap<String, CompConfig>, profile: String, type: ControllerType) {
        val stored = loadEntries(profile, type)
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
