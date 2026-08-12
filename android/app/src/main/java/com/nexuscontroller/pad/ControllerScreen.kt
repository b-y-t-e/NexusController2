package com.nexuscontroller.pad

import android.os.SystemClock
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Keyboard
import androidx.compose.material.icons.rounded.Menu
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.snapshots.SnapshotStateMap
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * How long the input loop waits between looks at the pad's own state.
 *
 * A floor, not a promise: the loop runs on the main thread, so a look happens
 * once the frame in front of it has been drawn. It bounds the delay a press can
 * suffer *here* — what it cannot do is make the thread free.
 */
private const val INPUT_POLL_MS = 4L

/** A resend even when nothing moved, so a lost packet cannot strand the pad. */
private const val IDLE_HEARTBEAT_MS = 250L

/**
 * The play surface: gamepad (mode 0), trackpad (mode 1) and racing wheel (mode 2).
 *
 * Button masks handed to [onInputChanged] are already the wire bits from [Protocol];
 * the visuals change with [controllerType] but the bits never do.
 */
@OptIn(ExperimentalComposeUiApi::class)
@Composable
fun PSControllerScreen(
    isConnected: Boolean,
    /** Guide must be held rather than tapped; see PSCenterButton. */
    guideHold: Boolean = true,
    showConnectionDialog: Boolean,
    currentMode: Int,
    controllerType: ControllerType,
    onToggleMenu: () -> Unit,
    configs: SnapshotStateMap<String, CompConfig>,
    themeMode: String,
    gyroRoll: Int,
    gyroPitch: Int,
    onSave: () -> Unit,
    onOpenConnection: () -> Unit,
    onCloseConnection: () -> Unit,
    onInputChanged: (Int, Int, Int, Int, Int, Int, Int, Int) -> Unit,
    onMouseMove: (Float, Float, Boolean, Boolean) -> Unit,
    onScroll: (Float, Float) -> Unit,
    onSendText: (String) -> Unit,
    triggerReset: Boolean,
    onResetDone: () -> Unit,
    onVibrate: () -> Unit,
    /** Reports the measured play surface in pixels; §10 documents carry it as `screen`. */
    onSurfaceMeasured: (Float, Float) -> Unit = { _, _ -> }
) {
    val scope = rememberCoroutineScope()
    val currentGyroRoll by rememberUpdatedState(gyroRoll)
    val currentOnInputChanged by rememberUpdatedState(onInputChanged)
    val vibrate by rememberUpdatedState(onVibrate)
    var steeringCenter by remember { mutableIntStateOf(0) }

    // Input state — sticks live in the UI's 0..255 space, 127 = centre.
    var leftX by remember { mutableIntStateOf(127) }
    var leftY by remember { mutableIntStateOf(127) }
    var rightX by remember { mutableIntStateOf(127) }
    var rightY by remember { mutableIntStateOf(127) }
    var leftTrigger by remember { mutableIntStateOf(0) }
    var rightTrigger by remember { mutableIntStateOf(0) }

    var turboPulse by remember { mutableStateOf(true) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(50)   // ~20 auto-clicks per second
            turboPulse = !turboPulse
        }
    }

    // "componentId:sub" -> mask
    val pressedLow = remember { mutableStateMapOf<String, Int>() }
    val pressedHigh = remember { mutableStateMapOf<String, Int>() }

    fun updateBtn(id: String, sub: String, mask: Int, isHigh: Boolean, pressed: Boolean) {
        val key = "$id:$sub"
        if (isHigh) {
            if (pressed) pressedHigh[key] = mask else pressedHigh.remove(key)
        } else {
            if (pressed) pressedLow[key] = mask else pressedLow.remove(key)
        }
    }

    // Leaving the pad must not leave a button stuck down — whether it is another
    // controller type taking its place or another mode entirely.
    //
    // A button reports its release from `tryAwaitRelease()`, inside the gesture
    // that is running while the finger is down. Switching to the trackpad takes
    // the pad out of the composition and cancels that gesture, so the release
    // never arrives and the frame keeps carrying the button. The pad state is
    // suppressed while the cursor is being driven, so the game does not see it —
    // but only for the one slot that holds the desktop lock, and only while
    // desktop control is on at all. Everywhere else the game holds that button
    // for as long as the phone stays on the trackpad.
    LaunchedEffect(controllerType, currentMode) {
        pressedLow.clear()
        pressedHigh.clear()
        leftX = 127; leftY = 127; rightX = 127; rightY = 127
        leftTrigger = 0; rightTrigger = 0
    }

    var isEditMode by remember { mutableStateOf(false) }
    var selectedId by remember { mutableStateOf<String?>(null) }

    // While you are playing, the pad is the whole interface: the chrome steps
    // aside a few seconds after you stop using it. It used to sit over the play
    // surface permanently, where the buzzer dome and the shoulder buttons ran
    // straight into it. Editing and connecting are deliberate acts, so there the
    // bar stays put.
    var chromeShown by remember { mutableStateOf(true) }
    // Bumped on every use, so the countdown restarts instead of running out
    // under the hand of someone still choosing.
    var chromeTouched by remember { mutableIntStateOf(0) }
    fun showChromeAgain() {
        chromeShown = true
        chromeTouched++
    }

    data class ThemeColors(
        val bg: Color,
        val bgGradientStart: Color,
        val bgGradientEnd: Color,
        val componentBg: Color,
        val componentStroke: Color,
        val text: Color,
        val accent: Color
    )

    val currentTheme = when (themeMode) {
        "Light" -> ThemeColors(
            bg = AppColors.BackgroundLight,
            bgGradientStart = Color(0xFFf5f6f8),
            bgGradientEnd = Color(0xFFd1d5db),
            componentBg = Color(0xFFE0E0E0),
            componentStroke = Color(0xFFCCCCCC),
            text = Color(0xFF333333),
            accent = AppColors.Primary
        )
        "Neon" -> ThemeColors(
            bg = Color.Black,
            bgGradientStart = Color.Black,
            bgGradientEnd = Color(0xFF050505),
            componentBg = Color(0xFF111111),
            componentStroke = AppColors.NeonBlue,
            text = AppColors.NeonBlue,
            accent = AppColors.NeonBlue
        )
        else -> ThemeColors(
            bg = Color(0xFF151515),
            bgGradientStart = Color(0xFF1a202e),
            bgGradientEnd = Color(0xFF101622),
            componentBg = Color(0xFF333333),
            componentStroke = Color(0xFF222222),
            text = Color(0xFFAAAAAA),
            accent = AppColors.Primary
        )
    }

    val componentBg = currentTheme.componentBg
    val componentStroke = currentTheme.componentStroke
    val textColor = currentTheme.text

    /*
     * Input is sent when it *changes*, not on a metronome.
     *
     * The old loop slept 15 ms between frames, so a press waited up to that long
     * before it was even written to the socket — 7.5 ms on average, added to
     * everything else, for every button on the pad. It also sent 66 identical
     * frames a second while nothing was happening.
     *
     * Looking every [INPUT_POLL_MS] and writing only on a change inverts both: a
     * press leaves as soon as the main thread comes back to this loop, and a
     * still pad sends nothing but the [IDLE_HEARTBEAT_MS] heartbeat. The channel
     * into the socket writer is `Channel.CONFLATED`, so even a fast burst cannot
     * back up — the newest state wins and the older ones are dropped unsent.
     *
     * Timed on [SystemClock.elapsedRealtime]: the wall clock can step when the
     * network corrects it, and a step backwards would hold the heartbeat for as
     * long as the correction — which the server would read as a phone that has
     * gone, and hand its slot to somebody else.
     */
    LaunchedEffect(Unit) {
        var lastSent: Triple<Int, Int, List<Int>>? = null
        var lastSentAt = 0L
        while (true) {
            val bl = pressedLow.asSequence().filter { (k, _) ->
                val id = k.split(":")[0]
                configs[id]?.isTurbo != true || turboPulse
            }.fold(0) { acc, (_, m) -> acc or m }

            val bh = pressedHigh.asSequence().filter { (k, _) ->
                val id = k.split(":")[0]
                configs[id]?.isTurbo != true || turboPulse
            }.fold(0) { acc, (_, m) -> acc or m }

            var lx = leftX
            var ly = leftY

            if (currentMode == 2) {
                // Racing wheel: steer from gyro roll around the calibrated centre.
                val rawVal = currentGyroRoll - steeringCenter
                lx = ((rawVal / 4500f).coerceIn(-1f, 1f) * 127.5f + 127.5f).toInt()
                ly = 127
            }

            val axes = listOf(lx, ly, rightX, rightY, leftTrigger, rightTrigger)
            val now = SystemClock.elapsedRealtime()
            val frame = Triple(bl, bh, axes)
            // The heartbeat exists so a server that missed a packet, or a pad
            // left untouched across a reconnect, cannot sit on a stale state.
            if (frame != lastSent || now - lastSentAt >= IDLE_HEARTBEAT_MS) {
                currentOnInputChanged(bl, bh, lx, ly, rightX, rightY, leftTrigger, rightTrigger)
                lastSent = frame
                lastSentAt = now
            }
            delay(INPUT_POLL_MS)
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                brush = Brush.radialGradient(
                    colors = listOf(currentTheme.bgGradientStart, currentTheme.bgGradientEnd, Color.Black),
                    center = Offset.Unspecified,
                    radius = 2000f
                )
            )
    ) {
        if (themeMode == "Dark") {
            CarbonBackgroundPattern()
        }

        // Anywhere on the bare surface. Controls consume their own touches, so
        // this only fires on the background — which no layout can cover.
        if (currentMode != 1) {
            Box(
                Modifier
                    .matchParentSize()
                    .pointerInput(Unit) {
                        detectTapGestures(onLongPress = { showChromeAgain() })
                    }
            )
        }

        if (currentMode != 1) {
            val chromeIsPinned = isEditMode || showConnectionDialog

            LaunchedEffect(chromeIsPinned, chromeShown, chromeTouched) {
                if (!chromeIsPinned && chromeShown) {
                    delay(3500)
                    chromeShown = false
                }
            }

            val chromeAlpha by animateFloatAsState(
                targetValue = if (chromeIsPinned || chromeShown) 1f else 0f,
                animationSpec = spring(stiffness = Spring.StiffnessLow),
                label = "chrome"
            )

            // Two ways back, because one is a trap: a control placed under the
            // strip would swallow every tap on it, and the user would be shut
            // inside play mode with no way to reach the menu. The long press on
            // the background is the one that cannot be covered up.
            if (chromeAlpha < 0.5f) {
                Box(
                    modifier = Modifier
                        .align(Alignment.TopCenter)
                        .zIndex(9f)
                        .size(width = 96.dp, height = 22.dp)
                        .clickable(
                            interactionSource = remember { MutableInteractionSource() },
                            indication = null
                        ) { showChromeAgain() },
                    contentAlignment = Alignment.Center
                ) {
                    Box(
                        Modifier
                            .size(width = 32.dp, height = 3.dp)
                            .background(Color.White.copy(alpha = 0.22f), CircleShape)
                    )
                }
            }

            Box(
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    // Above the play surface: a control drawn later must never
                    // paint over the chrome the way the Buzz dome did.
                    .zIndex(10f)
                    .alpha(chromeAlpha)
                    .fillMaxWidth()
                    .padding(horizontal = 24.dp, vertical = 8.dp)
                    .height(48.dp)
            ) {
                if (chromeAlpha > 0.05f) {
                    Box(
                        modifier = Modifier
                            .align(Alignment.CenterStart)
                            .size(44.dp)
                            .clip(CircleShape)
                            .background(AppColors.Surface)
                            .border(1.dp, Color.White.copy(alpha = 0.1f), CircleShape)
                            .clickable { showChromeAgain(); onToggleMenu() },
                        contentAlignment = Alignment.Center
                    ) {
                        Text("☰", color = Color.White.copy(alpha = 0.8f), fontSize = 20.sp)
                    }

                    Row(
                        modifier = Modifier
                            .align(Alignment.Center)
                            .height(44.dp)
                            .background(AppColors.Surface.copy(alpha = 0.8f), RoundedCornerShape(50))
                            .border(1.dp, Color.White.copy(alpha = 0.05f), RoundedCornerShape(50))
                            .padding(4.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        val play = stringResource(R.string.mode_play)
                        val edit = stringResource(R.string.mode_edit)
                        val save = stringResource(R.string.mode_save)
                        val connect = stringResource(R.string.mode_connect)
                        // Keyed by identity, labelled by translation: a Polish
                        // "EDYTUJ" must not stop matching the branch it drives.
                        listOf("PLAY" to play, "EDIT" to edit, "CONNECT" to connect)
                            .forEach { (item, label) ->
                            val displayLabel = if (item == "EDIT" && isEditMode) save else label
                            val isActive = when (item) {
                                "PLAY" -> !isEditMode && !showConnectionDialog
                                "EDIT" -> isEditMode
                                "CONNECT" -> showConnectionDialog
                                else -> false
                            }

                            Box(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(50))
                                    .background(if (isActive) AppColors.Primary else Color.Transparent)
                                    .clickable {
                                        showChromeAgain()
                                        when (item) {
                                            "PLAY" -> { if (isEditMode) onSave(); isEditMode = false; onCloseConnection() }
                                            "EDIT" -> { if (isEditMode) onSave(); isEditMode = !isEditMode; onCloseConnection() }
                                            "CONNECT" -> { onOpenConnection(); isEditMode = false }
                                        }
                                    }
                                    .padding(horizontal = 24.dp, vertical = 8.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Text(
                                    displayLabel,
                                    color = if (isActive) Color.White else Color.White.copy(alpha = 0.4f),
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.Bold,
                                    letterSpacing = 1.sp
                                )
                            }
                        }
                    }
                }
            }
        }

        if (currentMode == 0) {
            BoxWithConstraints(Modifier.fillMaxSize()) {
                val density = LocalDensity.current
                val w = with(density) { maxWidth.toPx() }
                val h = with(density) { maxHeight.toPx() }

                LaunchedEffect(w, h) { if (w > 0 && h > 0) onSurfaceMeasured(w, h) }

                LaunchedEffect(triggerReset, configs.isEmpty(), controllerType, w) {
                    if (w > 0 && (triggerReset || configs.isEmpty())) {
                        val newDefaults = LayoutStore.defaults(controllerType)
                        configs.clear()
                        newDefaults.forEach { (id, entry) -> configs[id] = CompConfig.from(entry) }
                        onSave()
                        onResetDone()
                    }
                }

                val getConf = { id: String -> configs[id] ?: CompConfig(0.5f, 0.5f) }

                if (isEditMode) EditorGridBackground(themeMode == "Light")

                CompositionLocalProvider(LocalLayoutSurface provides LayoutSurface(w, h)) {
                if (controllerType == ControllerType.BUZZ) {
                    BuzzLayout(
                        configs = configs,
                        isEditMode = isEditMode,
                        selectedId = selectedId,
                        onSelect = { selectedId = it },
                        onVibrate = { vibrate() },
                        onButton = { id, mask, pressed -> updateBtn(id, "", mask, false, pressed) }
                    )
                } else {
                    EditableComponent("L2", isEditMode, selectedId == "L2", getConf("L2"), { selectedId = it }) {
                        PSTriggerShape(Glyphs.trigger(controllerType, true), Modifier, componentBg, textColor, isLeft = true, onVibrate = { vibrate() }) { f ->
                            leftTrigger = (f * 255).toInt().coerceIn(0, 255)
                        }
                    }
                    EditableComponent("L1", isEditMode, selectedId == "L1", getConf("L1"), { selectedId = it }) {
                        PSBumperShape(Glyphs.bumper(controllerType, true), Modifier, componentBg, textColor, Protocol.BTN_LB, { vibrate() }) { m, p ->
                            updateBtn("L1", "", m, false, p)
                        }
                    }
                    EditableComponent("DPAD", isEditMode, selectedId == "DPAD", getConf("DPAD"), { selectedId = it }) {
                        PSDpadDetailed(componentBg, textColor) { dir, pressed ->
                            val mask = when (dir) {
                                0 -> Protocol.DPAD_UP
                                1 -> Protocol.DPAD_DOWN
                                2 -> Protocol.DPAD_LEFT
                                3 -> Protocol.DPAD_RIGHT
                                else -> 0
                            }
                            updateBtn("DPAD", dir.toString(), mask, true, pressed)
                            if (pressed) vibrate()
                        }
                    }
                    EditableComponent("L_STICK", isEditMode, selectedId == "L_STICK", getConf("L_STICK"), { selectedId = it }) {
                        PSJoystickSimple(
                            "L", componentBg, componentStroke,
                            onVibrate = { vibrate() },
                            onClick = { pressed -> updateBtn("L_STICK", "click", Protocol.BTN_L3, true, pressed) }
                        ) { x, y ->
                            leftX = ((x + 1) * 127.5).toInt().coerceIn(0, 255)
                            leftY = ((y + 1) * 127.5).toInt().coerceIn(0, 255)
                        }
                    }
                    EditableComponent("R2", isEditMode, selectedId == "R2", getConf("R2"), { selectedId = it }) {
                        PSTriggerShape(Glyphs.trigger(controllerType, false), Modifier, componentBg, textColor, isLeft = false, onVibrate = { vibrate() }) { f ->
                            rightTrigger = (f * 255).toInt().coerceIn(0, 255)
                        }
                    }
                    EditableComponent("R1", isEditMode, selectedId == "R1", getConf("R1"), { selectedId = it }) {
                        PSBumperShape(Glyphs.bumper(controllerType, false), Modifier, componentBg, textColor, Protocol.BTN_RB, { vibrate() }) { m, p ->
                            updateBtn("R1", "", m, false, p)
                        }
                    }
                    EditableComponent("FACE", isEditMode, selectedId == "FACE", getConf("FACE"), { selectedId = it }) {
                        PSFaceButtonsDetailed(componentBg, controllerType) { b, p ->
                            if (p) vibrate()
                            updateBtn("FACE", b.toString(), b, false, p)
                        }
                    }
                    EditableComponent("R_STICK", isEditMode, selectedId == "R_STICK", getConf("R_STICK"), { selectedId = it }) {
                        PSJoystickSimple(
                            "R", componentBg, componentStroke,
                            onVibrate = { vibrate() },
                            onClick = { pressed -> updateBtn("R_STICK", "click", Protocol.BTN_R3, true, pressed) }
                        ) { x, y ->
                            rightX = ((x + 1) * 127.5).toInt().coerceIn(0, 255)
                            rightY = ((y + 1) * 127.5).toInt().coerceIn(0, 255)
                        }
                    }
                    EditableComponent("SHARE", isEditMode, selectedId == "SHARE", getConf("SHARE"), { selectedId = it }) {
                        PSCenterButton(Glyphs.center(controllerType, "SHARE"), Modifier, componentBg, Protocol.BTN_BACK, false, isGuide = false, onVibrate = { vibrate() }) { m, p ->
                            updateBtn("SHARE", "", m, false, p)
                        }
                    }
                    EditableComponent("OPTIONS", isEditMode, selectedId == "OPTIONS", getConf("OPTIONS"), { selectedId = it }) {
                        PSCenterButton(Glyphs.center(controllerType, "OPTIONS"), Modifier, componentBg, Protocol.BTN_START, false, isGuide = false, onVibrate = { vibrate() }) { m, p ->
                            updateBtn("OPTIONS", "", m, false, p)
                        }
                    }
                    // Guide / PS button — buttons_high 0x40
                    EditableComponent("PS", isEditMode, selectedId == "PS", getConf("PS"), { selectedId = it }) {
                        GuideButton(isConnected) { pressed ->
                            if (pressed) vibrate()
                            updateBtn("PS", "", Protocol.BTN_GUIDE, true, pressed)
                        }
                    }

                    // Custom user buttons
                    configs.keys.toList().forEach { key ->
                        if (key.startsWith("BTN_")) {
                            val conf = configs[key] ?: return@forEach
                            EditableComponent(key, isEditMode, selectedId == key, conf, { selectedId = it }, { configs.remove(key); selectedId = null }) {
                                PSCenterButton("BTN", Modifier, componentBg, 0, false, isGuide = false, onVibrate = { vibrate() }) { _, pressed ->
                                    if (conf.mappedKey > 0) {
                                        if (pressed) onSendText(Char(conf.mappedKey).toString())
                                    }
                                }
                                if (conf.mappedKey > 0) {
                                    Text(Char(conf.mappedKey).toString(), Modifier.align(Alignment.Center), color = Color.White, fontWeight = FontWeight.Bold)
                                }
                            }
                        }
                    }
                }
                }

                if (isEditMode && selectedId != null) {
                    SizeStrip(configs[selectedId!!], Modifier.align(Alignment.BottomCenter))
                }
            }
        } else if (currentMode == 1) {
            TrackpadSurface(
                scope = scope,
                onToggleMenu = onToggleMenu,
                onMouseMove = onMouseMove,
                onScroll = onScroll,
                onSendText = onSendText
            )
        } else if (currentMode == 2) {
            RacingScreen(
                leftTrigger = leftTrigger,
                rightTrigger = rightTrigger,
                onSteer = { leftX = it },
                onBrake = { leftTrigger = it },
                onGas = { rightTrigger = it },
                onCalibrate = { steeringCenter = currentGyroRoll },
                onVibrate = { vibrate() }
            )
        }
    }
}

/** Big round Guide / PS button with the connection state ring. */
@Composable
private fun GuideButton(isConnected: Boolean, onEvent: (Boolean) -> Unit) {
    Box(
        modifier = Modifier
            .size(72.dp)
            .clip(CircleShape)
            .background(
                brush = Brush.linearGradient(
                    colors = listOf(AppColors.SurfaceHighlight, AppColors.Surface),
                    start = Offset(0f, 0f), end = Offset(72f, 72f)
                )
            )
            .border(1.dp, Color.White.copy(alpha = 0.1f), CircleShape)
            .shadow(10.dp, CircleShape)
            .pointerInput(Unit) {
                detectTapGestures(onPress = {
                    onEvent(true)
                    tryAwaitRelease()
                    onEvent(false)
                })
            },
        contentAlignment = Alignment.Center
    ) {
        val glowColor = if (isConnected) Color(0xFF22C55E) else Color(0xFFEF4444)
        Box(Modifier.fillMaxSize().background(glowColor.copy(alpha = 0.1f), CircleShape))

        Canvas(modifier = Modifier.size(36.dp)) {
            val c = glowColor
            val w = size.width
            val h = size.height
            val stroke = Stroke(width = 2.5f, cap = StrokeCap.Round, join = StrokeJoin.Round)

            val p = Path().apply {
                moveTo(w * 0.2f, h * 0.2f)
                quadraticBezierTo(w * 0.5f, h * 0.15f, w * 0.8f, h * 0.2f)
                quadraticBezierTo(w * 0.95f, h * 0.2f, w * 0.95f, h * 0.4f)
                lineTo(w * 0.95f, h * 0.6f)
                quadraticBezierTo(w * 0.95f, h * 0.85f, w * 0.75f, h * 0.8f)
                quadraticBezierTo(w * 0.65f, h * 0.75f, w * 0.6f, h * 0.6f)
                quadraticBezierTo(w * 0.5f, h * 0.55f, w * 0.4f, h * 0.6f)
                quadraticBezierTo(w * 0.35f, h * 0.75f, w * 0.25f, h * 0.8f)
                quadraticBezierTo(w * 0.05f, h * 0.85f, w * 0.05f, h * 0.6f)
                lineTo(w * 0.05f, h * 0.4f)
                quadraticBezierTo(w * 0.05f, h * 0.2f, w * 0.2f, h * 0.2f)
                close()
            }
            drawPath(p, c, style = stroke)

            val dpX = w * 0.3f
            val dpY = h * 0.45f
            val dpS = w * 0.08f
            drawLine(c, Offset(dpX, dpY - dpS), Offset(dpX, dpY + dpS), strokeWidth = 2f, cap = StrokeCap.Round)
            drawLine(c, Offset(dpX - dpS, dpY), Offset(dpX + dpS, dpY), strokeWidth = 2f, cap = StrokeCap.Round)

            val bX = w * 0.7f
            val bY = h * 0.45f
            val bR = 1.5f
            val bOff = w * 0.08f
            drawCircle(c, bR, Offset(bX, bY - bOff))
            drawCircle(c, bR, Offset(bX, bY + bOff))
            drawCircle(c, bR, Offset(bX - bOff, bY))
            drawCircle(c, bR, Offset(bX + bOff, bY))
        }
    }
}

/**
 * Buzz! buzzer: one big red dome plus the four coloured answer buttons. Every component is
 * individually placeable, so the layout editor keeps working.
 */
@Composable
private fun BuzzLayout(
    configs: SnapshotStateMap<String, CompConfig>,
    isEditMode: Boolean,
    selectedId: String?,
    onSelect: (String) -> Unit,
    onVibrate: () -> Unit,
    onButton: (String, Int, Boolean) -> Unit
) {
    val getConf = { id: String -> configs[id] ?: CompConfig(0.5f, 0.5f) }

    EditableComponent("BUZZ_RED", isEditMode, selectedId == "BUZZ_RED", getConf("BUZZ_RED"), onSelect) {
        BuzzBuzzerButton(stringResource(R.string.buzz_dome), onVibrate) { mask, pressed ->
            onButton("BUZZ_RED", mask, pressed)
        }
    }
    BuzzAnswerSpec.ALL.forEach { spec ->
        EditableComponent(spec.id, isEditMode, selectedId == spec.id, getConf(spec.id), onSelect) {
            BuzzAnswerButton(stringResource(spec.labelRes), spec.color, spec.mask, onVibrate) { mask, pressed ->
                onButton(spec.id, mask, pressed)
            }
        }
    }
}

/** The four answer buttons, in physical order: blue, orange, green, yellow. */
data class BuzzAnswerSpec(
    val id: String,
    /** Spoken by a screen reader; never drawn — the colour is the visible label. */
    @androidx.annotation.StringRes val labelRes: Int,
    val color: Color,
    val mask: Int
) {
    companion object {
        val ALL = listOf(
            BuzzAnswerSpec("BUZZ_BLUE", R.string.buzz_blue, AppColors.BuzzBlue, Protocol.BUZZ_BLUE),
            BuzzAnswerSpec("BUZZ_ORANGE", R.string.buzz_orange, AppColors.BuzzOrange, Protocol.BUZZ_ORANGE),
            BuzzAnswerSpec("BUZZ_GREEN", R.string.buzz_green, AppColors.BuzzGreen, Protocol.BUZZ_GREEN),
            BuzzAnswerSpec("BUZZ_YELLOW", R.string.buzz_yellow, AppColors.BuzzYellow, Protocol.BUZZ_YELLOW)
        )

        fun forId(id: String): BuzzAnswerSpec? = ALL.firstOrNull { it.id == id }
    }
}

@Composable
private fun SizeStrip(config: CompConfig?, modifier: Modifier) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .padding(start = 32.dp, end = 32.dp)
            .height(64.dp)
            .clip(RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp))
            .background(Color.Black.copy(alpha = 0.5f))
            .border(1.dp, Color.White.copy(alpha = 0.1f), RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp))
            .padding(horizontal = 24.dp)
            .zIndex(100f),
        contentAlignment = Alignment.Center
    ) {
        if (config == null) return@Box
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.editor_size), color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.width(16.dp))
            Slider(
                value = config.scale,
                onValueChange = { config.scale = it },
                valueRange = 0.5f..3.0f,
                modifier = Modifier.weight(1f),
                colors = SliderDefaults.colors(
                    thumbColor = AppColors.Primary,
                    activeTrackColor = AppColors.Primary,
                    inactiveTrackColor = Color.White.copy(alpha = 0.2f)
                )
            )
            Spacer(Modifier.width(16.dp))
            Text(
                "${String.format("%.1f", config.scale)}x",
                color = Color.White,
                fontSize = 12.sp,
                fontFamily = FontFamily.Monospace
            )
        }
    }
}

@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun TrackpadSurface(
    scope: kotlinx.coroutines.CoroutineScope,
    onToggleMenu: () -> Unit,
    onMouseMove: (Float, Float, Boolean, Boolean) -> Unit,
    onScroll: (Float, Float) -> Unit,
    onSendText: (String) -> Unit
) {
    val focusRequester = remember { FocusRequester() }
    var textState by remember { mutableStateOf(TextFieldValue(" ")) }
    val keyboardController = LocalSoftwareKeyboardController.current

    Box(Modifier.fillMaxSize()) {
        Canvas(Modifier.fillMaxSize()) {
            val step = 24.dp.toPx()
            for (x in 0..size.width.toInt() step step.toInt()) {
                for (y in 0..size.height.toInt() step step.toInt()) {
                    drawCircle(
                        color = Color(0xFF282E39).copy(alpha = 0.5f),
                        radius = 0.8.dp.toPx(),
                        center = Offset(x.toFloat(), y.toFloat())
                    )
                }
            }
        }

        Box(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(Unit) {
                    val density = this.density
                    awaitEachGesture {
                        awaitFirstDown()
                        var maxPointers = 1
                        var isDrag = false

                        do {
                            val event = awaitPointerEvent()
                            val activeChanges = event.changes.filter { it.pressed }
                            val currentPointers = activeChanges.size

                            if (currentPointers > maxPointers) {
                                if (currentPointers >= 2) {
                                    val p1 = activeChanges[0].position
                                    val p2 = activeChanges[1].position
                                    if ((p1 - p2).getDistance() / density > 25f) {
                                        maxPointers = currentPointers
                                    }
                                } else {
                                    maxPointers = currentPointers
                                }
                            }

                            var dx = 0f
                            var dy = 0f
                            var movingCount = 0
                            event.changes.forEach {
                                if (it.pressed) {
                                    dx += it.positionChange().x
                                    dy += it.positionChange().y
                                    movingCount++
                                }
                            }

                            if (movingCount > 0) {
                                dx /= movingCount
                                dy /= movingCount
                            }

                            if (dx != 0f || dy != 0f) {
                                if (!isDrag && (dx * dx + dy * dy) > 1.5f) isDrag = true

                                if (isDrag) {
                                    event.changes.forEach { if (it.positionChange() != Offset.Zero) it.consume() }
                                    if (maxPointers >= 2) {
                                        onScroll(dx * 0.8f, dy * 0.8f)
                                    } else {
                                        onMouseMove(dx, dy, false, false)
                                    }
                                }
                            }
                        } while (event.changes.any { it.pressed })

                        onMouseMove(0f, 0f, false, false)

                        if (!isDrag) {
                            if (maxPointers == 1) {
                                scope.launch {
                                    onMouseMove(0f, 0f, true, false)
                                    delay(35)
                                    onMouseMove(0f, 0f, false, false)
                                }
                            } else if (maxPointers >= 2) {
                                scope.launch {
                                    onMouseMove(0f, 0f, false, true)
                                    delay(35)
                                    onMouseMove(0f, 0f, false, false)
                                }
                            }
                        }
                    }
                }
        )

        IconButton(
            onClick = { onToggleMenu() },
            modifier = Modifier
                .padding(16.dp)
                .align(Alignment.TopStart)
                .background(Color.Black, CircleShape)
        ) {
            Icon(Icons.Rounded.Menu, null, tint = Color.Gray)
        }

        val isImeVisible = WindowInsets.ime.getBottom(LocalDensity.current) > 0
        Box(
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .imePadding()
                .padding(24.dp)
                .size(56.dp)
                .shadow(12.dp, CircleShape, spotColor = if (isImeVisible) Color.Red else Color(0xFF0D59F2))
                .background(if (isImeVisible) Color.Red else Color(0xFF0D59F2), CircleShape)
                .clickable {
                    if (isImeVisible) {
                        keyboardController?.hide()
                    } else {
                        focusRequester.requestFocus()
                        keyboardController?.show()
                    }
                },
            contentAlignment = Alignment.Center
        ) {
            Icon(if (isImeVisible) Icons.Rounded.Close else Icons.Rounded.Keyboard, null, tint = Color.White)
        }

        // Hidden, space-buffered field: lets us detect backspace as well as characters.
        TextField(
            value = textState,
            onValueChange = { newValue ->
                val text = newValue.text
                if (text.isEmpty()) {
                    onSendText("\b")
                    textState = TextFieldValue(" ", selection = TextRange(1))
                } else if (text == " ") {
                    textState = newValue.copy(selection = TextRange(1))
                } else {
                    val typed = text.filter { it != ' ' }
                    if (typed.isNotEmpty()) onSendText(typed)
                    textState = TextFieldValue(" ", selection = TextRange(1))
                }
            },
            modifier = Modifier
                .size(1.dp)
                .alpha(0f)
                .focusRequester(focusRequester),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { onSendText("\n") })
        )
    }
}

@Composable
private fun RacingScreen(
    leftTrigger: Int,
    rightTrigger: Int,
    onSteer: (Int) -> Unit,
    onBrake: (Int) -> Unit,
    onGas: (Int) -> Unit,
    onCalibrate: () -> Unit,
    onVibrate: () -> Unit
) {
    var rotation by remember { mutableFloatStateOf(0f) }
    var speed by remember { mutableIntStateOf(0) }
    var gear by remember { mutableIntStateOf(1) }

    Box(Modifier.fillMaxSize().background(AppColors.CarbonDark)) {
        Canvas(Modifier.fillMaxSize()) {
            val stepSize = 20.dp.toPx()
            for (x in 0..size.width.toInt() step stepSize.toInt()) {
                for (y in 0..size.height.toInt() step stepSize.toInt()) {
                    drawRect(
                        color = Color.White.copy(alpha = 0.02f),
                        topLeft = Offset(x.toFloat(), y.toFloat()),
                        size = Size(stepSize / 2, stepSize / 2)
                    )
                }
            }
            drawCircle(AppColors.NeonBlue.copy(alpha = 0.05f), radius = 400f, center = Offset(size.width * 0.2f, size.height * 0.5f))
            drawCircle(AppColors.NeonRed.copy(alpha = 0.05f), radius = 300f, center = Offset(size.width * 0.8f, size.height * 0.5f))
        }

        Row(
            modifier = Modifier.fillMaxSize().padding(horizontal = 32.dp, vertical = 16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(Modifier.weight(1.2f), contentAlignment = Alignment.Center) {
                RacingWheel(rotation = rotation) { newRot ->
                    rotation = newRot
                    onSteer((((newRot / 135f) + 1f) * 127.5f).toInt().coerceIn(0, 255))
                }
                Text(
                    stringResource(R.string.racing_tap_to_centre),
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .clickable { onCalibrate() }
                        .padding(8.dp),
                    color = AppColors.NeonBlue.copy(alpha = 0.6f),
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Black,
                    letterSpacing = 2.sp
                )
            }

            Box(Modifier.weight(0.8f), contentAlignment = Alignment.Center) {
                RacingDashboard(speed = speed, gear = gear)

                LaunchedEffect(rightTrigger, leftTrigger) {
                    while (true) {
                        if (rightTrigger > 0) {
                            speed = (speed + (rightTrigger / 50)).coerceIn(0, 299)
                        } else if (speed > 0) {
                            speed = (speed - 2).coerceAtLeast(0)
                        }
                        if (leftTrigger > 0) {
                            speed = (speed - (leftTrigger / 20)).coerceAtLeast(0)
                        }
                        gear = when {
                            speed < 30 -> 1
                            speed < 70 -> 2
                            speed < 120 -> 3
                            speed < 180 -> 4
                            speed < 240 -> 5
                            else -> 6
                        }
                        delay(50)
                    }
                }
            }

            Row(
                modifier = Modifier.weight(1f).fillMaxHeight(),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalAlignment = Alignment.Bottom
            ) {
                RacingPedal(stringResource(R.string.racing_clutch), Modifier.fillMaxHeight(0.7f), color = AppColors.NeonYellow, width = 60.dp) { }
                RacingPedal(stringResource(R.string.racing_brake), Modifier.fillMaxHeight(0.85f), color = AppColors.NeonRed, width = 100.dp) { v ->
                    onBrake((v * 255).toInt())
                    if (v > 0) onVibrate()
                }
                RacingPedal("GAS", Modifier.fillMaxHeight(0.95f), color = AppColors.NeonBlue, width = 80.dp) { v ->
                    onGas((v * 255).toInt())
                }
            }
        }
    }
}

@Composable
fun KeyboardDialog(
    onDismiss: () -> Unit,
    onSend: (String) -> Unit
) {
    var text by remember { mutableStateOf("") }

    androidx.compose.ui.window.Dialog(onDismissRequest = onDismiss) {
        Card(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(containerColor = Color.White)
        ) {
            Column(Modifier.padding(16.dp)) {
                Text(stringResource(R.string.keyboard_title), fontSize = 20.sp, fontWeight = FontWeight.Bold, color = Color.Black)
                Spacer(Modifier.height(16.dp))

                TextField(
                    value = text,
                    onValueChange = { text = it },
                    placeholder = { Text(stringResource(R.string.keyboard_hint)) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = false,
                    maxLines = 4
                )

                Spacer(Modifier.height(16.dp))
                Row(horizontalArrangement = Arrangement.End, modifier = Modifier.fillMaxWidth()) {
                    Button(onClick = onDismiss, colors = ButtonDefaults.buttonColors(containerColor = Color.Gray)) {
                        Text(stringResource(R.string.action_cancel))
                    }
                    Spacer(Modifier.width(8.dp))
                    Button(onClick = { if (text.isNotEmpty()) onSend(text) }) {
                        Text(stringResource(R.string.keyboard_send))
                    }
                }
            }
        }
    }
}
