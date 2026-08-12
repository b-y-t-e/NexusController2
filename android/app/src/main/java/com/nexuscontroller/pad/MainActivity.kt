package com.nexuscontroller.pad

import android.content.Context
import android.content.SharedPreferences
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import kotlin.math.roundToInt

class MainActivity : ComponentActivity(), SensorEventListener {

    private val networkController = NetworkController()
    private lateinit var prefs: SharedPreferences
    private lateinit var profilesPrefs: SharedPreferences
    private lateinit var layoutStore: LayoutStore

    private lateinit var sensorManager: SensorManager
    private var sensor: Sensor? = null

    private var gyroRoll by mutableIntStateOf(0)
    private var gyroPitch by mutableIntStateOf(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("layout_prefs", MODE_PRIVATE)
        profilesPrefs = getSharedPreferences("profiles_list", MODE_PRIVATE)
        // The display size is only needed to migrate pixel-based layouts written by older
        // builds; the composable refines it with the real play surface once it is measured.
        layoutStore = LayoutStore(prefs, profilesPrefs, displayScreenSize())

        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        sensor = sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            androidx.core.view.WindowCompat.setDecorFitsSystemWindows(window, false)
            val insetsController = androidx.core.view.WindowCompat.getInsetsController(window, window.decorView)
            insetsController.systemBarsBehavior = androidx.core.view.WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            insetsController.hide(androidx.core.view.WindowInsetsCompat.Type.systemBars())
        } else {
            @Suppress("DEPRECATION")
            window.setFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN, WindowManager.LayoutParams.FLAG_FULLSCREEN)
        }

        setContent {
            val context = LocalContext.current
            val haptics = remember { Haptics(context) }
            val wifi = remember { LowLatencyWifi(context) }

            var isConnected by remember { mutableStateOf(false) }
            var showMenu by remember { mutableStateOf(false) }
            var currentMode by remember { mutableIntStateOf(0) }   // 0=Controller, 1=Trackpad, 2=Racing
            var showSettings by remember { mutableStateOf(false) }
            var showHelp by remember { mutableStateOf(false) }
            var showAbout by remember { mutableStateOf(false) }
            var globalToastMessage by remember { mutableStateOf<String?>(null) }

            // ---- settings ----
            var themeMode by remember { mutableStateOf(prefs.getString("theme_mode", "Dark") ?: "Dark") }
            var keepScreenOn by remember { mutableStateOf(prefs.getBoolean("keep_screen_on", true)) }
            var hapticEnabled by remember { mutableStateOf(prefs.getBoolean("haptic_enabled", true)) }
            var hapticStrength by remember { mutableFloatStateOf(prefs.getFloat("haptic_strength", 0.85f)) }
            var gyroEnabled by remember { mutableStateOf(prefs.getBoolean("gyro_enabled", false)) }
            var gyroSensitivity by remember { mutableFloatStateOf(prefs.getFloat("gyro_sensitivity", 0.4f)) }
            var touchVibration by remember { mutableStateOf(prefs.getBoolean("touch_vibration", true)) }
            var autoReconnect by remember { mutableStateOf(prefs.getBoolean("auto_reconnect", true)) }
            var deviceName by remember { mutableStateOf(prefs.getString("device_name", "Player 1") ?: "Player 1") }
            var touchSensitivity by remember { mutableFloatStateOf(prefs.getFloat("touch_sensitivity", 1.0f)) }
            // On by default: Guide means "go home" to Windows, and the middle of
            // a pad is where a thumb passes.
            var guideHold by remember { mutableStateOf(prefs.getBoolean("guide_hold", true)) }
            // Off by default: the gestures are always available, and the bar
            // costs a strip of the pad. It exists for hands that never meet them.
            var trackpadButtons by remember {
                mutableStateOf(prefs.getBoolean("trackpad_buttons", false))
            }
            var controllerType by remember { mutableStateOf(layoutStore.controllerType()) }
            var needsFirstRunChoice by remember { mutableStateOf(!layoutStore.hasChosenControllerType()) }

            if (needsFirstRunChoice) {
                FirstRunScreen { chosen ->
                    controllerType = chosen
                    layoutStore.setControllerType(chosen)
                    needsFirstRunChoice = false
                }
                return@setContent
            }

            // ---- connection ----
            var target by remember { mutableStateOf(loadSavedTarget()) }
            // A rejected pairing code must not be retried in a loop.
            var handshakeBlocked by remember { mutableStateOf(false) }

            LaunchedEffect(keepScreenOn) {
                if (keepScreenOn) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }

            // Latest settings for the network callbacks, which are installed once.
            val currentHapticEnabled by rememberUpdatedState(hapticEnabled)
            val currentHapticStrength by rememberUpdatedState(hapticStrength)

            // Ticked by every accepted handshake; §10 wants a CONFIG straight after WELCOME.
            var welcomeTick by remember { mutableIntStateOf(0) }

            DisposableEffect(Unit) {
                networkController.onStateChanged = { state ->
                    isConnected = state == NetworkController.State.CONNECTED
                    if (isConnected) {
                        handshakeBlocked = false
                        // Held only while a pad is actually in use; it costs battery.
                        wifi.acquire()
                    } else {
                        haptics.cancel()
                        wifi.release()
                    }
                }
                networkController.onRumble = { large, small ->
                    val strength = maxOf(large, small)
                    if (currentHapticEnabled) haptics.rumble(strength, currentHapticStrength)
                    else haptics.cancel()
                }
                networkController.onLed = { r, g, b ->
                    ledColor = if (r == 0 && g == 0 && b == 0) null else Color(r, g, b)
                }
                networkController.onWelcome = { welcome ->
                    globalToastMessage = getString(R.string.connect_connected, welcome.slot + 1)
                    welcomeTick++
                }
                networkController.onRejected = { reason ->
                    // Retrying a bad token or a rate limit only makes things worse.
                    handshakeBlocked = reason == RejectReason.INVALID_TOKEN ||
                        reason == RejectReason.RATE_LIMITED ||
                        reason == RejectReason.UNSUPPORTED_VERSION
                }
                networkController.onError = { msg -> globalToastMessage = friendlyError(msg) }
                onDispose {
                    wifi.release()
                    networkController.onStateChanged = null
                    networkController.onRumble = null
                    networkController.onLed = null
                    networkController.onWelcome = null
                    networkController.onRejected = null
                    networkController.onError = null
                }
            }

            // Auto-reconnect: only dials when idle, never while a connect is in flight.
            LaunchedEffect(target, autoReconnect, controllerType, handshakeBlocked) {
                while (true) {
                    if (!isConnected && autoReconnect && !handshakeBlocked && target != null) {
                        networkController.connectIfIdle(target, controllerType, deviceName)
                    }
                    kotlinx.coroutines.delay(3000)
                }
            }

            val configs = remember { mutableStateMapOf<String, CompConfig>() }
            var showLayoutManager by remember { mutableStateOf(false) }
            var currentProfileName by remember { mutableStateOf(layoutStore.activeProfile()) }
            var profileList by remember { mutableStateOf(layoutStore.profileList()) }

            LaunchedEffect(currentProfileName, controllerType) {
                layoutStore.load(configs, currentProfileName, controllerType)
                layoutStore.setActiveProfile(currentProfileName)
            }

            var showConnectionDialog by remember { mutableStateOf(false) }
            var triggerReset by remember { mutableStateOf(false) }

            var gyroRollOffset by remember { mutableIntStateOf(0) }
            var gyroPitchOffset by remember { mutableIntStateOf(0) }

            var showLayoutEditor by remember { mutableStateOf(false) }
            var isCreatingNew by remember { mutableStateOf(false) }
            var showNameDialog by remember { mutableStateOf(false) }
            val tempConfigsForSave = remember { mutableMapOf<String, CompConfig>() }

            // ---- configuration documents (PROTOCOL.md §10) ----

            var screenSize by remember { mutableStateOf(layoutStore.screen) }
            // Bumped whenever something the PC should know about changed; the debounced sender
            // below turns a burst of edits into a single CONFIG.
            var configRevision by remember { mutableIntStateOf(0) }

            fun currentConfigDocument(): ConfigDocument = ConfigDocument(
                version = ConfigCodec.SCHEMA_VERSION,
                type = controllerType,
                name = deviceName,
                screen = screenSize,
                layout = configs.mapValues { it.value.toEntry() },
                settings = ConfigSettings(
                    haptics = hapticEnabled,
                    hapticStrength = hapticStrength,
                    gyro = gyroEnabled,
                    gyroSensitivity = gyroSensitivity,
                    touchVibration = touchVibration,
                    theme = themeMode
                )
            )

            fun sendConfigNow() {
                networkController.sendConfig(ConfigCodec.encode(currentConfigDocument()))
            }

            LaunchedEffect(welcomeTick) { if (welcomeTick > 0) sendConfigNow() }

            // §10: report the current appearance right after WELCOME, then after every change.
            // Debounced, because dragging a component produces a change per frame.
            LaunchedEffect(
                isConnected, configRevision, screenSize, controllerType, deviceName, themeMode,
                hapticEnabled, hapticStrength, gyroEnabled, gyroSensitivity, touchVibration
            ) {
                if (!isConnected) return@LaunchedEffect
                kotlinx.coroutines.delay(CONFIG_DEBOUNCE_MS)
                sendConfigNow()
            }

            fun applyPushedSettings(s: ConfigSettings) {
                val editor = prefs.edit()
                s.theme?.let { themeMode = it; editor.putString("theme_mode", it) }
                s.haptics?.let { hapticEnabled = it; editor.putBoolean("haptic_enabled", it) }
                s.hapticStrength?.let { hapticStrength = it; editor.putFloat("haptic_strength", it) }
                s.gyro?.let { gyroEnabled = it; editor.putBoolean("gyro_enabled", it) }
                s.gyroSensitivity?.let { gyroSensitivity = it; editor.putFloat("gyro_sensitivity", it) }
                s.touchVibration?.let { touchVibration = it; editor.putBoolean("touch_vibration", it) }
                editor.apply()
            }

            /**
             * Applies a `SET_CONFIG` pushed from the PC and persists it. Whatever the document
             * does not mention is left exactly as it was, and an echo is scheduled so the PC
             * can confirm what actually landed.
             */
            fun applyPushedConfig(doc: ConfigDocument) {
                // A face is not a device. DUALSHOCK3 and DUALSHOCK4 share one wire
                // type, so a document saying DUALSHOCK4 carries no opinion about
                // which of the two the phone should wear — the PC cannot express
                // the difference and its designer does not even offer it. Treating
                // it as a change would silently undo the user's choice of face and
                // redial the connection for nothing, which now costs the session
                // its slot as well.
                val newType = controllerType.faceFor(doc.type)
                val typeChanged = newType != controllerType

                if (doc.layout != null) {
                    val stored = if (typeChanged) layoutStore.loadEntries(currentProfileName, newType)
                    else configs.mapValues { it.value.toEntry() }
                    val base = stored.ifEmpty { LayoutStore.defaults(newType) }
                    val merged = ConfigCodec.mergeLayout(base, doc.layout, newType)
                    layoutStore.saveEntries(merged, currentProfileName, newType)
                    if (!typeChanged) {
                        configs.clear()
                        merged.forEach { (id, entry) -> configs[id] = CompConfig.from(entry) }
                    }
                }

                doc.settings?.let { applyPushedSettings(it) }

                doc.name?.takeIf { it.isNotBlank() && it != deviceName }?.let {
                    deviceName = it
                    prefs.edit().putString("device_name", it).apply()
                }

                if (typeChanged) {
                    // Keep the layout of the family we are leaving; the reload effect keyed on
                    // controllerType will pull the merged one back in.
                    layoutStore.save(configs, currentProfileName, controllerType)
                    controllerType = newType
                    layoutStore.setControllerType(newType)
                    // Only ever reached when the wire type really differs, so the
                    // HELLO the server has on file is now wrong: redial.
                    networkController.disconnect()
                    if (target != null) networkController.connect(target, newType, deviceName)
                }

                globalToastMessage = getString(R.string.notice_layout_from_pc)
                configRevision++
            }

            DisposableEffect(Unit) {
                networkController.onSetConfig = { json ->
                    val doc = ConfigCodec.parse(json)
                    if (doc == null) {
                        android.util.Log.w("Nexus", "SET_CONFIG ignored: malformed or unsupported schema version")
                    } else {
                        applyPushedConfig(doc)
                    }
                }
                onDispose { networkController.onSetConfig = null }
            }

            fun applyTarget(newTarget: ConnectionTarget) {
                saveTarget(newTarget)
                target = newTarget
                handshakeBlocked = false
                networkController.disconnect()
                networkController.connect(newTarget, controllerType, deviceName)
            }

            fun switchControllerType(type: ControllerType) {
                if (type == controllerType) return
                layoutStore.save(configs, currentProfileName, controllerType)
                controllerType = type
                layoutStore.setControllerType(type)
                // The device type is announced in HELLO, so the session has to be redialled.
                networkController.disconnect()
                if (target != null) networkController.connect(target, type, deviceName)
                // No toast: the pad on screen has just become that controller.
                // Announcing it covered the mode bar to say what the user is
                // already looking at.
            }

            PSControllerScreen(
                isConnected = isConnected,
                guideHold = guideHold,
                trackpadButtons = trackpadButtons,
                showConnectionDialog = showConnectionDialog,
                currentMode = currentMode,
                controllerType = controllerType,
                onToggleMenu = { showMenu = true },
                configs = configs,
                themeMode = themeMode,
                gyroRoll = gyroRoll,
                gyroPitch = gyroPitch,
                onSave = {
                    layoutStore.save(configs, currentProfileName, controllerType)
                    configRevision++
                },
                onOpenConnection = { showConnectionDialog = true },
                onCloseConnection = { showConnectionDialog = false },
                onInputChanged = { bl, bh, lx, ly, rx, ry, lt, rt ->
                    val gRoll = if (gyroEnabled) ((gyroRoll - gyroRollOffset) * gyroSensitivity).toInt() else 0
                    val gPitch = if (gyroEnabled) ((gyroPitch - gyroPitchOffset) * gyroSensitivity).toInt() else 0
                    networkController.sendInput(
                        lx, ly, rx, ry, bl, bh, lt, rt, gRoll, gPitch,
                        mouseMode = currentMode == 1,
                        gyroValid = gyroEnabled
                    )
                },
                onMouseMove = { dx, dy, l, r -> networkController.sendMouse(dx, dy, l, r, touchSensitivity) },
                onScroll = { dx, dy -> networkController.sendScroll(dx, dy, touchSensitivity) },
                onSendText = { text -> networkController.sendText(text) },
                triggerReset = triggerReset,
                onResetDone = { triggerReset = false },
                onVibrate = { haptics.tap(touchVibration, hapticStrength) },
                onSurfaceMeasured = { w, h ->
                    val measured = ScreenSize(w.roundToInt(), h.roundToInt())
                    if (measured != screenSize) {
                        screenSize = measured
                        layoutStore.screen = measured
                    }
                }
            )

            if (showMenu) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color.Black.copy(alpha = 0.4f))
                        .blur(8.dp)
                        .clickable(
                            interactionSource = remember { MutableInteractionSource() },
                            indication = null
                        ) { showMenu = false }
                        .zIndex(15f)
                )
            }

            StitchSidebar(
                isVisible = showMenu,
                currentMode = currentMode,
                controllerType = controllerType,
                onControllerTypeChange = { switchControllerType(it) },
                onModeSelect = { mode ->
                    currentMode = mode
                    showMenu = false
                    showSettings = false
                    showHelp = false
                    showAbout = false
                },
                isConnected = isConnected,
                onConnect = {
                    showMenu = false
                    showConnectionDialog = true
                },
                onDisconnect = {
                    networkController.disconnect()
                    target = null
                    showMenu = false
                    showConnectionDialog = false
                },
                onDismiss = { showMenu = false },
                onSettingsClick = { showMenu = false; showSettings = true },
                onHelpClick = { showMenu = false; showHelp = true },
                onAboutClick = { showMenu = false; showAbout = true },
                onLayoutsClick = { showMenu = false; showLayoutManager = true },
                themeMode = themeMode
            )

            if (showHelp) {
                Box(modifier = Modifier.zIndex(100f).fillMaxSize()) {
                    HelpScreen(onBack = { showHelp = false }, themeMode = themeMode)
                }
            }

            if (showAbout) {
                Box(modifier = Modifier.zIndex(100f).fillMaxSize()) {
                    AboutScreen(onBack = { showAbout = false }, themeMode = themeMode)
                }
            }

            SettingsScreen(
                isVisible = showSettings,
                onBack = {
                    showSettings = false
                    layoutStore.save(configs, currentProfileName, controllerType)
                    configRevision++
                },
                state = SettingsState(
                    themeMode = themeMode,
                    keepScreenOn = keepScreenOn,
                    hapticEnabled = hapticEnabled,
                    hapticStrength = hapticStrength,
                    gyroEnabled = gyroEnabled,
                    gyroSensitivity = gyroSensitivity,
                    touchVibration = touchVibration,
                    guideHold = guideHold,
                    trackpadButtons = trackpadButtons,
                    autoReconnect = autoReconnect,
                    deviceName = deviceName,
                    touchSensitivity = touchSensitivity,
                    controllerType = controllerType
                ),
                onThemeChange = { mode ->
                    themeMode = mode
                    prefs.edit().putString("theme_mode", mode).apply()
                },
                onScreenOnToggle = {
                    keepScreenOn = it
                    prefs.edit().putBoolean("keep_screen_on", it).apply()
                },
                onHapticToggle = {
                    hapticEnabled = it
                    prefs.edit().putBoolean("haptic_enabled", it).apply()
                },
                onHapticStrengthChange = {
                    hapticStrength = it
                    prefs.edit().putFloat("haptic_strength", it).apply()
                },
                onGyroToggle = {
                    gyroEnabled = it
                    prefs.edit().putBoolean("gyro_enabled", it).apply()
                },
                onGyroSensitivityChange = {
                    gyroSensitivity = it
                    prefs.edit().putFloat("gyro_sensitivity", it).apply()
                },
                onCalibrateGyro = {
                    gyroRollOffset = gyroRoll
                    gyroPitchOffset = gyroPitch
                    globalToastMessage = getString(R.string.notice_gyro_calibrated)
                },
                onGuideHoldToggle = {
                    guideHold = it
                    prefs.edit().putBoolean("guide_hold", it).apply()
                },
                onTrackpadButtonsToggle = {
                    trackpadButtons = it
                    prefs.edit().putBoolean("trackpad_buttons", it).apply()
                },
                onTouchVibrationToggle = {
                    touchVibration = it
                    prefs.edit().putBoolean("touch_vibration", it).apply()
                },
                onAutoReconnectToggle = {
                    autoReconnect = it
                    prefs.edit().putBoolean("auto_reconnect", it).apply()
                },
                onDeviceNameChange = {
                    deviceName = it
                    prefs.edit().putString("device_name", it).apply()
                },
                onTouchSensitivityChange = {
                    touchSensitivity = it
                    prefs.edit().putFloat("touch_sensitivity", it).apply()
                },
                onControllerTypeChange = { switchControllerType(it) },
                onSave = { showSettings = false },
                onReset = {
                    themeMode = "Dark"
                    keepScreenOn = true
                    hapticEnabled = true
                    hapticStrength = 0.85f
                    gyroEnabled = false
                    gyroSensitivity = 0.4f
                    touchVibration = true
                    autoReconnect = true
                    touchSensitivity = 1.0f
                    gyroRollOffset = 0
                    gyroPitchOffset = 0

                    prefs.edit()
                        .putString("theme_mode", "Dark")
                        .putBoolean("keep_screen_on", true)
                        .putBoolean("haptic_enabled", true)
                        .putFloat("haptic_strength", 0.85f)
                        .putBoolean("gyro_enabled", false)
                        .putFloat("gyro_sensitivity", 0.4f)
                        .putBoolean("touch_vibration", true)
                        .putBoolean("auto_reconnect", true)
                        .putFloat("touch_sensitivity", 1.0f)
                        .apply()
                }
            )

            if (showConnectionDialog) {
                Box(Modifier.fillMaxSize().zIndex(100f)) {
                    StitchConnectionScreen(
                        currentIp = target?.ip ?: "",
                        onDismiss = { showConnectionDialog = false },
                        onConnect = { raw ->
                            val parsed = resolveTarget(raw)
                            if (parsed == null) {
                                globalToastMessage =
                                    getString(R.string.error_bad_address)
                            } else {
                                applyTarget(parsed)
                                showConnectionDialog = false
                            }
                        },
                        onConnectDiscovered = { ip, port ->
                            val parsed = resolveTarget(ip)?.copy(port = port)
                            if (parsed == null) {
                                globalToastMessage = getString(R.string.error_bad_address)
                            } else {
                                applyTarget(parsed)
                                showConnectionDialog = false
                            }
                        },
                        onQrScan = {
                            val options = GmsBarcodeScannerOptions.Builder()
                                .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                                .enableAutoZoom()
                                .build()
                            val scanner = GmsBarcodeScanning.getClient(this@MainActivity, options)
                            scanner.startScan()
                                .addOnSuccessListener { barcode ->
                                    val parsed = QrPayload.parse(barcode.rawValue)
                                    if (parsed == null) {
                                        globalToastMessage =
                                            getString(R.string.error_bad_qr)
                                    } else {
                                        applyTarget(parsed)
                                        showConnectionDialog = false
                                    }
                                }
                                .addOnFailureListener { e ->
                                    globalToastMessage = getString(R.string.error_scan_failed)
                                    android.util.Log.e("QRScan", "Scan failed", e)
                                }
                        },
                        onStartDiscovery = { cb -> networkController.startDiscovery(cb) },
                        onStopDiscovery = { networkController.stopDiscovery() },
                        themeMode = themeMode
                    )
                }
            }

            if (showLayoutManager) {
                Box(modifier = Modifier.zIndex(100f).fillMaxSize()) {
                    LayoutManagerScreen(
                        layouts = profileList,
                        activeProfile = currentProfileName,
                        onBack = { showLayoutManager = false },
                        onCreate = {
                            isCreatingNew = true
                            showLayoutEditor = true
                            showLayoutManager = false
                        },
                        onSelect = { name -> currentProfileName = name },
                        onEdit = { name ->
                            currentProfileName = name
                            isCreatingNew = false
                            showLayoutEditor = true
                            showLayoutManager = false
                        },
                        onRename = { oldName, newName ->
                            if (layoutStore.renameProfile(oldName, newName)) {
                                if (currentProfileName == oldName) currentProfileName = newName
                                profileList = layoutStore.profileList()
                            }
                        },
                        onDelete = { name ->
                            layoutStore.deleteProfile(name)
                            if (currentProfileName == name) {
                                currentProfileName = layoutStore.profileList().firstOrNull()
                                    ?: LayoutStore.DEFAULT_PROFILE
                            }
                            profileList = layoutStore.profileList()
                        },
                        themeMode = themeMode
                    )
                }
            }

            if (showLayoutEditor) {
                Box(Modifier.fillMaxSize().zIndex(200f)) {
                    LayoutEditorScreen(
                        initialConfigs = configs.toMap(),
                        controllerType = controllerType,
                        onBack = { showLayoutEditor = false; isCreatingNew = false },
                        onSave = { edited ->
                            tempConfigsForSave.clear()
                            tempConfigsForSave.putAll(edited)
                            if (isCreatingNew) {
                                showNameDialog = true
                            } else {
                                layoutStore.save(edited, currentProfileName, controllerType)
                                configs.clear()
                                configs.putAll(edited)
                                configRevision++
                                showLayoutEditor = false
                            }
                        },
                        themeMode = themeMode
                    )
                }
            }

            if (showNameDialog) {
                InputDialog(
                    title = "Name Your Layout",
                    initialValue = "",
                    isLight = themeMode == "Light",
                    onDismiss = { showNameDialog = false },
                    onConfirm = { name ->
                        layoutStore.save(tempConfigsForSave, name, controllerType)
                        currentProfileName = name
                        profileList = layoutStore.profileList()
                        configRevision++
                        showNameDialog = false
                        showLayoutEditor = false
                        isCreatingNew = false
                    }
                )
            }

            // DS4 lightbar / Buzz lamp colour pushed by the PC.
            ledColor?.let { c ->
                Box(
                    Modifier
                        .fillMaxWidth()
                        .height(4.dp)
                        .background(c)
                        .zIndex(2001f)
                )
            }

            GlobalToast(globalToastMessage) { globalToastMessage = null }
        }
    }

    /** Lightbar colour last requested by the PC — DS4 lightbar or Buzz lamp. */
    private var ledColor by mutableStateOf<Color?>(null)

    /** Display size in pixels, used before the play surface has been measured. */
    private fun displayScreenSize(): ScreenSize {
        val dm = resources.displayMetrics
        return ScreenSize(dm.widthPixels.coerceAtLeast(1), dm.heightPixels.coerceAtLeast(1))
    }

    // ---------------------------------------------------------------- targets

    private fun loadSavedTarget(): ConnectionTarget? {
        val ip = prefs.getString("last_ip", null)?.takeIf { QrPayload.isIpv4(it) } ?: return null
        val port = prefs.getInt("last_port", Protocol.DEFAULT_PORT)
        return ConnectionTarget(ip, port, tokenFor(ip))
    }

    private fun saveTarget(t: ConnectionTarget) {
        val editor = prefs.edit()
            .putString("last_ip", t.ip)
            .putInt("last_port", t.port)
        // Tokens are stored per server IP so reconnects are automatic (protocol §8).
        if (t.token.isNotEmpty()) editor.putString("token_${t.ip}", t.token)
        editor.apply()
    }

    private fun tokenFor(ip: String): String = prefs.getString("token_$ip", "") ?: ""

    /**
     * Validates user/QR/scan input and fills in a token kept from an earlier pairing.
     * Returns null when the input is neither a `NEXUSPAD2:` payload nor a bare IPv4.
     * The rule itself lives in [QrPayload.targetFor], where it can be tested.
     */
    private fun resolveTarget(raw: String): ConnectionTarget? =
        QrPayload.targetFor(raw, ::tokenFor)

    /**
     * Turns a socket exception into something worth reading.
     *
     * The *matching* stays on the English text java.net produces — that is what
     * arrives regardless of the phone's language — while the *answer* comes from
     * resources, so it is the reader's language.
     */
    /** Turns "reject:<code>" into the reason, in the reader's language. */
    private fun rejectMessage(msg: String): String {
        val code = msg.removePrefix(NetworkController.REJECT_PREFIX).toIntOrNull()
            ?: return getString(R.string.error_disconnected)
        val reason = RejectReason.fromCode(code)
        return if (reason != null) getString(reason.messageRes)
        else getString(R.string.reject_unknown, code)
    }

    private fun friendlyError(msg: String): String = when {
        msg.startsWith(NetworkController.REJECT_PREFIX) -> rejectMessage(msg)
        msg.contains("failed to connect", ignoreCase = true) ->
            getString(R.string.error_no_pc)
        msg.contains("Connection refused", ignoreCase = true) ->
            getString(R.string.error_refused)
        msg.contains("timeout", ignoreCase = true) || msg.contains("timed out", ignoreCase = true) ->
            getString(R.string.error_timeout)
        msg.contains("Network is unreachable", ignoreCase = true) ->
            getString(R.string.error_no_network)
        msg.contains("EOF", ignoreCase = true) || msg.contains("closed the connection", ignoreCase = true) ->
            getString(R.string.notice_server_closed)
        // Never the raw exception. "java.net.SocketException: Software caused
        // connection abort" tells the reader nothing they can act on.
        else -> getString(R.string.error_connection)
    }

    // ---------------------------------------------------------------- lifecycle

    override fun onDestroy() {
        super.onDestroy()
        networkController.disconnect()
    }

    override fun onResume() {
        super.onResume()
        sensor?.also { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME) }
    }

    override fun onPause() {
        super.onPause()
        sensorManager.unregisterListener(this)
    }

    override fun onSensorChanged(event: SensorEvent?) {
        if (event?.sensor?.type == Sensor.TYPE_ROTATION_VECTOR) {
            val rotationMatrix = FloatArray(9)
            SensorManager.getRotationMatrixFromVector(rotationMatrix, event.values)

            // Landscape remap: side buttons up/down.
            val remappedMatrix = FloatArray(9)
            SensorManager.remapCoordinateSystem(rotationMatrix, SensorManager.AXIS_Y, SensorManager.AXIS_MINUS_X, remappedMatrix)

            val orientation = FloatArray(3)
            SensorManager.getOrientation(remappedMatrix, orientation)

            gyroPitch = (orientation[1] * 10000).toInt()
            gyroRoll = (orientation[2] * 10000).toInt()
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    private companion object {
        /** Dragging a component fires a change per frame; the PC only needs the result. */
        const val CONFIG_DEBOUNCE_MS = 500L
    }
}
