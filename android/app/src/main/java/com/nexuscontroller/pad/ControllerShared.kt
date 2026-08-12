package com.nexuscontroller.pad

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Info
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

object AppColors {
    val BackgroundDark = Color(0xFF101622)
    val BackgroundGradientStart = Color(0xFF1a202e)
    val BackgroundGradientEnd = Color.Black
    val Surface = Color(0xFF1A1D26)
    val SurfaceHighlight = Color(0xFF2a2f3a)
    val Primary = Color(0xFF0d59f2)
    val Accent = Color(0xFF3b82f6)
    val TextWhite = Color.White
    val TextGray = Color(0xFFAAAAAA)

    // Stitch Colors
    val GlowBlue = Color(0x660D59F2)
    val GlowGreen = Color(0x6622C55E)

    // Racing Colors
    val NeonBlue = Color(0xFF00F3FF)
    val NeonRed = Color(0xFFFF003C)
    val NeonYellow = Color(0xFFFBBF24)
    val CarbonDark = Color(0xFF080808)

    val BackgroundLight = Color(0xFFf5f6f8)

    // Buzz buzzer colours
    val BuzzRed = Color(0xFFE01B24)
    val BuzzBlue = Color(0xFF1C71D8)
    val BuzzOrange = Color(0xFFFF7800)
    val BuzzGreen = Color(0xFF2EC27E)
    val BuzzYellow = Color(0xFFF5C211)

    // Xbox face button colours
    val XboxA = Color(0xFF22C55E)
    val XboxB = Color(0xFFEF4444)
    val XboxX = Color(0xFF3B82F6)
    val XboxY = Color(0xFFFBBF24)
}

/**
 * Which physical face button a glyph stands for. The wire bit never changes with the
 * controller type — only the drawing does.
 */
enum class FacePosition(val mask: Int) {
    BOTTOM(Protocol.BTN_A),
    RIGHT(Protocol.BTN_B),
    LEFT(Protocol.BTN_X),
    TOP(Protocol.BTN_Y)
}

/** Per-controller-type labels; the layout component IDs stay the same. */
object Glyphs {
    fun bumper(type: ControllerType, left: Boolean): String = when (type) {
        ControllerType.DUALSHOCK4, ControllerType.DUALSHOCK3 -> if (left) "L1" else "R1"
        else -> if (left) "LB" else "RB"
    }

    fun trigger(type: ControllerType, left: Boolean): String = when (type) {
        ControllerType.DUALSHOCK4, ControllerType.DUALSHOCK3 -> if (left) "L2" else "R2"
        else -> if (left) "LT" else "RT"
    }

    /** `SHARE` component is Back on Xbox, `OPTIONS` is Start. */
    fun center(type: ControllerType, id: String): String = when (type) {
        // The DualShock 3 kept SELECT and START; the 4 renamed them.
        ControllerType.DUALSHOCK3 -> if (id == "SHARE") "SELECT" else "START"
        ControllerType.DUALSHOCK4 -> if (id == "SHARE") "SHARE" else "OPTIONS"
        else -> if (id == "SHARE") "BACK" else "START"
    }

    fun faceLetter(type: ControllerType, pos: FacePosition): String =
        if (type.isPlayStation) {
            when (pos) {
                FacePosition.BOTTOM -> "CROSS"
                FacePosition.RIGHT -> "CIRCLE"
                FacePosition.LEFT -> "SQUARE"
                FacePosition.TOP -> "TRIANGLE"
            }
        } else {
            when (pos) {
                FacePosition.BOTTOM -> "A"
                FacePosition.RIGHT -> "B"
                FacePosition.LEFT -> "X"
                FacePosition.TOP -> "Y"
            }
        }

    fun faceColor(type: ControllerType, pos: FacePosition): Color =
        if (type.isPlayStation) {
            when (pos) {
                FacePosition.BOTTOM -> Color(0xFF3B82F6)   // cross
                FacePosition.RIGHT -> Color(0xFFEF4444)    // circle
                FacePosition.LEFT -> Color(0xFFEC4899)     // square
                FacePosition.TOP -> Color(0xFF22C55E)      // triangle
            }
        } else {
            when (pos) {
                FacePosition.BOTTOM -> AppColors.XboxA
                FacePosition.RIGHT -> AppColors.XboxB
                FacePosition.LEFT -> AppColors.XboxX
                FacePosition.TOP -> AppColors.XboxY
            }
        }
}

/**
 * Size of the surface components are placed on, in pixels.
 *
 * Placement is normalised (PROTOCOL.md §10), so every component needs to know how big the
 * play area is. Both the play screen and the layout editor publish their measured
 * `BoxWithConstraints` here; the fallback exists only so a preview cannot divide by zero.
 */
data class LayoutSurface(val width: Float, val height: Float) {
    val isMeasured: Boolean get() = width > 0f && height > 0f
    val safeWidth: Float get() = width.coerceAtLeast(1f)
    val safeHeight: Float get() = height.coerceAtLeast(1f)
}

val LocalLayoutSurface = compositionLocalOf { LayoutSurface(0f, 0f) }

/**
 * Layout config for one on-screen component, as observable Compose state.
 *
 * [x] and [y] are the **normalised centre** of the component — fractions of the play surface
 * in `0..1`, exactly as PROTOCOL.md §10 defines them — not pixels and not a corner. The
 * conversion to a pixel top-left happens at draw time in [EditableComponent], where the
 * component's own measured size is known.
 */
class CompConfig(
    initialX: Float,
    initialY: Float,
    initialScale: Float = 1f,
    initialRotation: Float = 0f,
    initialKey: Int = 0,
    initialTurbo: Boolean = false
) {
    var x by mutableFloatStateOf(initialX)
    var y by mutableFloatStateOf(initialY)
    var scale by mutableFloatStateOf(initialScale)
    var rotation by mutableFloatStateOf(initialRotation)
    var mappedKey by mutableIntStateOf(initialKey)
    var isTurbo by mutableStateOf(initialTurbo)

    fun copy() = CompConfig(x, y, scale, rotation, mappedKey, isTurbo)

    fun toEntry() = LayoutEntry(x, y, scale, rotation, mappedKey, isTurbo)

    companion object {
        fun from(entry: LayoutEntry): CompConfig {
            val e = LayoutBounds.clamp(entry)
            return CompConfig(e.x, e.y, e.scale, e.rotation, e.mappedKey, e.isTurbo)
        }
    }
}

@Composable
fun EditableComponent(
    id: String,
    isEditMode: Boolean,
    isSelected: Boolean,
    config: CompConfig,
    onSelect: (String) -> Unit,
    onDelete: () -> Unit = {},
    content: @Composable () -> Unit
) {
    val surface = LocalLayoutSurface.current
    // The intrinsic (unscaled) size of the component: `scale` and `rotate` are draw-only, so
    // the measured box is what the normalised centre has to be resolved against.
    var measured by remember { mutableStateOf(androidx.compose.ui.unit.IntSize.Zero) }

    Box(
        modifier = Modifier
            .onSizeChanged { measured = it }
            .offset {
                // Until the first measurement lands, fall back to the nominal footprint of
                // PROTOCOL.md §10 — the same number the PC preview uses for this component.
                val nominal = ComponentSizes.nominalPx(id, surface.safeHeight)
                val cw = if (measured.width > 0) measured.width.toFloat() else nominal
                val ch = if (measured.height > 0) measured.height.toFloat() else nominal
                IntOffset(
                    (config.x * surface.safeWidth - cw / 2f).roundToInt(),
                    (config.y * surface.safeHeight - ch / 2f).roundToInt()
                )
            }
            .rotate(config.rotation)
            .scale(config.scale)
            // The gesture nodes sit inside both graphics layers, so `pan` arrives in the
            // component's own (scaled, rotated) space. Map local -> parent with the same
            // transform — rotate(scale(pan)) — then to a fraction of the surface.
            .pointerInput(isEditMode) { if (isEditMode) detectTapGestures { onSelect(id) } }
            .pointerInput(isEditMode, surface) {
                if (isEditMode) {
                    detectTransformGestures { _, pan, _, _ ->
                        if (!isSelected) onSelect(id)
                        val sx = pan.x * config.scale
                        val sy = pan.y * config.scale
                        val rad = Math.toRadians(config.rotation.toDouble())
                        val c = cos(rad).toFloat()
                        val s = sin(rad).toFloat()
                        config.x = LayoutBounds.position(
                            config.x + (sx * c - sy * s) / surface.safeWidth
                        )
                        config.y = LayoutBounds.position(
                            config.y + (sx * s + sy * c) / surface.safeHeight
                        )
                    }
                }
            }
            .then(
                if (isEditMode && isSelected) {
                    Modifier
                        .shadow(12.dp, RoundedCornerShape(8.dp), spotColor = AppColors.Primary)
                        .border(2.dp, AppColors.Primary, RoundedCornerShape(8.dp))
                        .background(AppColors.Primary.copy(alpha = 0.05f), RoundedCornerShape(8.dp))
                } else if (isEditMode) {
                    Modifier.border(1.dp, Color.Yellow.copy(alpha = 0.3f), RoundedCornerShape(4.dp))
                } else Modifier
            )
    ) {
        content()

        if (isEditMode) {
            if (isSelected) {
                if (id.startsWith("BTN_")) {
                    Box(
                        modifier = Modifier
                            .align(Alignment.TopEnd)
                            .offset(x = 12.dp, y = (-12).dp)
                            .size(24.dp)
                            .background(Color.Red, CircleShape)
                            .border(1.dp, Color.White, CircleShape)
                            .pointerInput(Unit) { detectTapGestures { onDelete() } }
                    ) {
                        Text("X", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold, modifier = Modifier.align(Alignment.Center))
                    }
                }

                if (id.startsWith("BTN_") || id.startsWith("BUZZ_") ||
                    id in listOf("FACE", "DPAD", "L1", "R1", "L2", "R2")
                ) {
                    Box(
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .offset(x = 12.dp, y = 12.dp)
                            .size(24.dp)
                            .background(if (config.isTurbo) Color.Green else Color.Gray, CircleShape)
                            .border(1.dp, Color.White, CircleShape)
                            .pointerInput(Unit) { detectTapGestures { config.isTurbo = !config.isTurbo } }
                    ) {
                        Text("T", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold, modifier = Modifier.align(Alignment.Center))
                    }
                }

                Box(
                    modifier = Modifier
                        .align(Alignment.Center)
                        .offset(y = 40.dp)
                        .background(Color.Black.copy(alpha = 0.7f), RoundedCornerShape(4.dp))
                        .padding(horizontal = 4.dp, vertical = 2.dp)
                ) {
                    Text(
                        "∠${config.rotation.toInt()}° | ${String.format("%.1f", config.scale)}x",
                        color = Color.White,
                        fontSize = 10.sp,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold
                    )
                }
            }
        }
    }
}

@Composable
fun PSTriggerShape(
    label: String,
    modifier: Modifier,
    bg: Color,
    txt: Color,
    isLeft: Boolean = label.startsWith("L"),
    /** A tick when a thumb lands, as the bumpers and the sticks already give. */
    onVibrate: () -> Unit = {},
    onValue: (Float) -> Unit
) {
    var triggerValue by remember { mutableFloatStateOf(0f) }
    val brush = Brush.linearGradient(colors = listOf(AppColors.SurfaceHighlight, AppColors.Surface))
    val shape = if (isLeft) RoundedCornerShape(topStart = 4.dp, topEnd = 4.dp, bottomStart = 24.dp, bottomEnd = 4.dp)
    else RoundedCornerShape(topStart = 4.dp, topEnd = 4.dp, bottomStart = 4.dp, bottomEnd = 24.dp)

    Box(
        modifier = modifier
            .shadow(4.dp, shape)
            .size(90.dp, 60.dp)
            .clip(shape)
            .background(brush)
            .border(1.dp, Color.White.copy(alpha = 0.1f), shape)
            .pointerInput(Unit) {
                awaitEachGesture {
                    val down = awaitFirstDown()
                    // Once, on the way down. A trigger reports a value the whole
                    // time a thumb slides along it, and buzzing on every one of
                    // those would be a rattle, not feedback.
                    onVibrate()
                    val height = size.height.toFloat()
                    var y = down.position.y.coerceIn(0f, height)
                    triggerValue = y / height
                    onValue(triggerValue)

                    var dragging = true
                    while (dragging) {
                        val event = awaitPointerEvent()
                        val change = event.changes.find { it.id == down.id }
                        if (change != null && change.pressed) {
                            y = change.position.y.coerceIn(0f, height)
                            triggerValue = y / height
                            onValue(triggerValue)
                        } else dragging = false
                    }
                    triggerValue = 0f
                    onValue(0f)
                }
            }
    ) {
        Box(modifier = Modifier.fillMaxWidth().fillMaxHeight(triggerValue).align(Alignment.TopCenter).background(AppColors.Primary.copy(alpha = 0.4f)))
        Text(label, Modifier.align(Alignment.Center), color = Color.White.copy(alpha = 0.6f), fontWeight = FontWeight.Bold, fontSize = 14.sp, letterSpacing = 2.sp)
        Box(modifier = Modifier.align(Alignment.BottomStart).fillMaxWidth().height(4.dp).background(Color.White.copy(alpha = 0.05f)))
    }
}

@Composable
fun PSBumperShape(
    label: String,
    modifier: Modifier,
    bg: Color,
    txt: Color,
    mask: Int,
    onVibrate: () -> Unit,
    onEvent: (Int, Boolean) -> Unit
) {
    var isPressed by remember { mutableStateOf(false) }

    val brush = Brush.linearGradient(
        colors = if (isPressed) listOf(AppColors.Primary, AppColors.Accent) else listOf(AppColors.SurfaceHighlight, AppColors.Surface)
    )

    Box(
        modifier = modifier
            .size(90.dp, 40.dp)
            .shadow(if (isPressed) 8.dp else 4.dp, RoundedCornerShape(8.dp), spotColor = AppColors.Primary)
            .clip(RoundedCornerShape(8.dp))
            .background(brush)
            .border(1.dp, Color.White.copy(alpha = 0.1f), RoundedCornerShape(8.dp))
            .pointerInput(Unit) {
                detectTapGestures(onPress = {
                    isPressed = true
                    onVibrate()
                    onEvent(mask, true)
                    tryAwaitRelease()
                    isPressed = false
                    onEvent(mask, false)
                })
            }
    ) {
        Text(label, Modifier.align(Alignment.Center), color = if (isPressed) Color.White else Color.White.copy(alpha = 0.7f), fontWeight = FontWeight.Bold, fontSize = 14.sp, letterSpacing = 2.sp)
    }
}

@Composable
fun PSDpadDetailed(bg: Color, txt: Color, onPress: (Int, Boolean) -> Unit) {
    Box(
        modifier = Modifier
            .size(180.dp)
            .background(AppColors.Surface.copy(alpha = 0.2f), CircleShape)
            .border(1.dp, Color.White.copy(alpha = 0.05f), CircleShape)
            .padding(10.dp),
        contentAlignment = Alignment.Center
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Spacer(Modifier.size(50.dp))
                DpadButton("arrow_drop_up", 0, onPress)
                Spacer(Modifier.size(50.dp))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                DpadButton("arrow_left", 2, onPress)
                Spacer(Modifier.size(50.dp))
                DpadButton("arrow_right", 3, onPress)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Spacer(Modifier.size(50.dp))
                DpadButton("arrow_drop_down", 1, onPress)
                Spacer(Modifier.size(50.dp))
            }
        }
    }
}

@Composable
fun DpadButton(icon: String, dir: Int, onPress: (Int, Boolean) -> Unit) {
    var isPressed by remember { mutableStateOf(false) }
    val brush = Brush.linearGradient(colors = if (isPressed) listOf(AppColors.Primary, AppColors.Accent) else listOf(AppColors.SurfaceHighlight, AppColors.Surface))
    Box(
        modifier = Modifier
            .shadow(if (isPressed) 12.dp else 4.dp, RoundedCornerShape(12.dp), spotColor = AppColors.Primary)
            .size(50.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(brush)
            .border(1.dp, Color.White.copy(alpha = 0.1f), RoundedCornerShape(12.dp))
            .pointerInput(Unit) { detectTapGestures(onPress = { isPressed = true; onPress(dir, true); tryAwaitRelease(); isPressed = false; onPress(dir, false) }) },
        contentAlignment = Alignment.Center
    ) {
        val arrow = when (icon) { "arrow_drop_up" -> "▲"; "arrow_drop_down" -> "▼"; "arrow_left" -> "◄"; "arrow_right" -> "►"; else -> "?" }
        Text(arrow, color = if (isPressed) Color.White else Color.White.copy(alpha = 0.5f), fontSize = 20.sp)
    }
}

/** How long the Guide button must be held before it fires. */
const val GUIDE_HOLD_MS = 600L

/**
 * SHARE / OPTIONS / PS (Guide). [label] is already localised for the controller type;
 * [isGuide] selects the big round Guide button styling.
 */
@Composable
fun PSCenterButton(
    label: String,
    modifier: Modifier = Modifier,
    bg: Color,
    mask: Int,
    isConnected: Boolean = false,
    isGuide: Boolean = label.uppercase() == "PS" || label.uppercase() == "GUIDE",
    /** Only meaningful for the Guide button; every other control is a plain tap. */
    requireHold: Boolean = true,
    onVibrate: () -> Unit,
    onEvent: (Int, Boolean) -> Unit
) {
    var isPressed by remember { mutableStateOf(false) }
    // Guide is held, not tapped. Windows and every launcher treat it as "go
    // home": one stray touch during a game drops you to the desktop, and it is
    // in the middle of the pad where a thumb passes. Nothing else on the pad
    // costs that much to press by accident, so nothing else asks for a hold.
    var isArming by remember { mutableStateOf(false) }

    val accentColor = if (isGuide) {
        if (isConnected) Color(0xFF22C55E) else Color(0xFFEF4444)
    } else Color.White.copy(alpha = 0.6f)

    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = modifier) {
        Box(
            modifier = Modifier
                .size(if (isGuide) 68.dp else 44.dp, if (isGuide) 68.dp else 26.dp)
                .shadow(if (isPressed) 16.dp else 4.dp, if (isGuide) CircleShape else RoundedCornerShape(50), spotColor = if (isGuide) accentColor else Color.Black)
                .clip(if (isGuide) CircleShape else RoundedCornerShape(50))
                .background(brush = if (isGuide) Brush.linearGradient(listOf(AppColors.SurfaceHighlight, AppColors.Surface)) else Brush.verticalGradient(listOf(AppColors.SurfaceHighlight, AppColors.Surface)))
                .border(2.dp, if (isGuide && isPressed) accentColor else Color.White.copy(alpha = 0.1f), if (isGuide) CircleShape else RoundedCornerShape(50))
                .pointerInput(isGuide, requireHold) {
                    detectTapGestures(onPress = {
                        if (isGuide && requireHold) {
                            // Held: nothing is sent until the hold completes, so
                            // letting go early is a cancel, not a press.
                            isArming = true
                            val held = withTimeoutOrNull(GUIDE_HOLD_MS) { tryAwaitRelease() } == null
                            isArming = false
                            if (held) {
                                isPressed = true
                                onVibrate()
                                onEvent(mask, true)
                                tryAwaitRelease()
                                isPressed = false
                                onEvent(mask, false)
                            }
                        } else {
                            isPressed = true
                            onVibrate()
                            onEvent(mask, true)
                            tryAwaitRelease()
                            isPressed = false
                            onEvent(mask, false)
                        }
                    })
                },
            contentAlignment = Alignment.Center
        ) {
            // The ring fills while the hold counts down, so the wait is visible
            // rather than a button that seems not to work.
            if (isGuide && isArming) {
                val sweep by animateFloatAsState(
                    targetValue = 360f,
                    animationSpec = tween(GUIDE_HOLD_MS.toInt(), easing = LinearEasing),
                    label = "guide-arm"
                )
                Canvas(Modifier.size(64.dp)) {
                    drawArc(
                        color = accentColor,
                        startAngle = -90f,
                        sweepAngle = sweep,
                        useCenter = false,
                        style = Stroke(width = 6f, cap = StrokeCap.Round)
                    )
                }
            }
            if (isGuide) {
                Canvas(modifier = Modifier.size(28.dp)) {
                    val c = if (isPressed) Color.White else accentColor
                    val sw = 5f
                    val w = size.width
                    val h = size.height
                    val p = Path().apply {
                        moveTo(0f, h * 0.4f); quadraticBezierTo(0f, 0f, w * 0.3f, 0f); lineTo(w * 0.7f, 0f); quadraticBezierTo(w, 0f, w, h * 0.4f)
                        lineTo(w * 0.9f, h); quadraticBezierTo(w * 0.8f, h * 0.7f, w * 0.6f, h * 0.7f); lineTo(w * 0.4f, h * 0.7f)
                        quadraticBezierTo(w * 0.2f, h * 0.7f, w * 0.1f, h); close()
                    }
                    drawPath(p, c, style = Stroke(width = sw, join = StrokeJoin.Round))
                }
            } else {
                Text(
                    label,
                    color = Color.White.copy(alpha = if (isPressed) 0.95f else 0.5f),
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Black,
                    letterSpacing = 1.sp
                )
            }
        }
    }
}

/** The four face buttons, drawn as Xbox letters or PlayStation shapes. Wire bits never move. */
@Composable
fun PSFaceButtonsDetailed(bg: Color, type: ControllerType = ControllerType.XBOX360, onPress: (Int, Boolean) -> Unit) {
    Box(
        modifier = Modifier
            .size(180.dp)
            .background(AppColors.Surface.copy(alpha = 0.2f), CircleShape)
            .border(1.dp, Color.White.copy(alpha = 0.05f), CircleShape)
            .padding(10.dp),
        contentAlignment = Alignment.Center
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Spacer(Modifier.size(50.dp))
                FaceButton(FacePosition.TOP, type, Modifier.size(50.dp), onPress); Spacer(Modifier.size(50.dp))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FaceButton(FacePosition.LEFT, type, Modifier.size(50.dp), onPress); Spacer(Modifier.size(50.dp))
                FaceButton(FacePosition.RIGHT, type, Modifier.size(50.dp), onPress)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Spacer(Modifier.size(50.dp))
                FaceButton(FacePosition.BOTTOM, type, Modifier.size(50.dp), onPress); Spacer(Modifier.size(50.dp))
            }
        }
    }
}

@Composable
fun FaceButton(
    position: FacePosition,
    type: ControllerType,
    modifier: Modifier = Modifier,
    onEvent: (Int, Boolean) -> Unit
) {
    var isPressed by remember { mutableStateOf(false) }
    val glowColor = Glyphs.faceColor(type, position)
    val brush = Brush.linearGradient(
        colors = if (isPressed) listOf(glowColor, glowColor.copy(alpha = 0.8f))
        else listOf(AppColors.SurfaceHighlight, AppColors.Surface)
    )
    Box(
        modifier = modifier
            .shadow(if (isPressed) 12.dp else 4.dp, CircleShape, spotColor = glowColor)
            .clip(CircleShape).background(brush).border(1.dp, Color.White.copy(alpha = 0.1f), CircleShape)
            .pointerInput(position, type) {
                detectTapGestures(onPress = {
                    isPressed = true; onEvent(position.mask, true)
                    tryAwaitRelease()
                    isPressed = false; onEvent(position.mask, false)
                })
            },
        contentAlignment = Alignment.Center
    ) {
        if (type.isPlayStation) {
            Canvas(modifier = Modifier.size(24.dp)) {
                val c = if (isPressed) Color.White else glowColor
                val sw = 6f
                val w = size.width
                val h = size.height
                when (position) {
                    FacePosition.TOP -> drawPath(Path().apply { moveTo(w / 2, 0f); lineTo(w, h); lineTo(0f, h); close() }, c, style = Stroke(width = sw, join = StrokeJoin.Round))
                    FacePosition.RIGHT -> drawCircle(c, style = Stroke(width = sw))
                    FacePosition.BOTTOM -> {
                        drawLine(c, Offset(0f, 0f), Offset(w, h), sw, cap = StrokeCap.Round)
                        drawLine(c, Offset(w, 0f), Offset(0f, h), sw, cap = StrokeCap.Round)
                    }
                    FacePosition.LEFT -> drawRect(c, style = Stroke(width = sw))
                }
            }
        } else {
            Text(
                Glyphs.faceLetter(type, position),
                color = if (isPressed) Color.White else glowColor,
                fontSize = 22.sp,
                fontWeight = FontWeight.Black
            )
        }
    }
}

/**
 * Analogue stick. Dragging reports `-1..1` (Y downwards, converted on the wire);
 * a tap without movement is the stick click (L3/R3).
 */
@Composable
fun PSJoystickSimple(
    label: String,
    bg: Color,
    stroke: Color,
    /** A short tick when a thumb lands on the stick, as every button already gives. */
    onVibrate: () -> Unit = {},
    onClick: (Boolean) -> Unit = {},
    onMoved: (Float, Float) -> Unit
) {
    var knobPosition by remember { mutableStateOf(Offset.Zero) }
    var containerSize by remember { mutableStateOf(androidx.compose.ui.unit.IntSize.Zero) }
    val isMoving = knobPosition != Offset.Zero
    Box(
        modifier = Modifier.size(110.dp).onSizeChanged { containerSize = it }.background(Color(0xFF0F1218), CircleShape).border(2.dp, Color.White.copy(alpha = 0.05f), CircleShape).shadow(15.dp, CircleShape, clip = false),
        contentAlignment = Alignment.Center
    ) {
        Box(modifier = Modifier.offset { IntOffset(knobPosition.x.toInt(), knobPosition.y.toInt()) }.size(60.dp), contentAlignment = Alignment.Center) {
            Box(Modifier.fillMaxSize().background(Brush.verticalGradient(listOf(Color(0xFF454C5B), Color(0xFF1A1D24))), CircleShape).border(1.5.dp, Color.White.copy(alpha = 0.12f), CircleShape))
            Box(Modifier.fillMaxSize(0.65f).background(Brush.radialGradient(0f to Color(0xFF333A47), 1f to Color(0xFF10141C)), CircleShape), contentAlignment = Alignment.Center) {
                Text(if (label == "L") "L3" else "R3", color = if (isMoving) AppColors.Accent else Color.White.copy(alpha = 0.35f), fontSize = 10.sp, fontWeight = FontWeight.Black)
            }
        }
        Box(
            Modifier
                .fillMaxSize()
                .pointerInput(Unit) {
                    detectTapGestures(onPress = {
                        onVibrate()
                        onClick(true)
                        tryAwaitRelease()
                        onClick(false)
                    })
                }
                .pointerInput(Unit) {
                    // A drag that starts on the stick never becomes a tap, so the
                    // tick has to be given here too or a thumb that goes straight
                    // into a turn feels nothing at all.
                    detectDragGestures(onDragStart = { onVibrate() }, onDragEnd = { knobPosition = Offset.Zero; onMoved(0f, 0f) }, onDrag = { change, dragAmount ->
                        change.consume()
                        val radius = containerSize.width / 2f
                        if (radius > 0) {
                            val newPos = knobPosition + dragAmount
                            val distance = sqrt(newPos.x * newPos.x + newPos.y * newPos.y)
                            val maxDist = radius * 0.75f
                            knobPosition = if (distance > maxDist) {
                                val angle = atan2(newPos.y, newPos.x); Offset(cos(angle) * maxDist, sin(angle) * maxDist)
                            } else newPos
                            onMoved(knobPosition.x / maxDist, knobPosition.y / maxDist)
                        }
                    })
                }
        )
    }
}

// ------------------------------------------------------------------ Buzz buzzer

/**
 * The big red dome on top of a Buzz! buzzer. Sends the semantic RED bit — the PC maps it
 * to XInput RIGHT_SHOULDER for RPCS3.
 *
 * Wordless, like the hardware: a dome this size, this red, above four coloured answer
 * buttons, is not something anyone has to read a label to understand.
 */
@Composable
fun BuzzBuzzerButton(label: String, onVibrate: () -> Unit, onEvent: (Int, Boolean) -> Unit) {
    var isPressed by remember { mutableStateOf(false) }
    val dome = if (isPressed) AppColors.BuzzRed else AppColors.BuzzRed.copy(alpha = 0.92f)

    Box(
        modifier = Modifier
            .semantics { contentDescription = label }
            .size(190.dp)
            .pointerInput(Unit) {
                detectTapGestures(onPress = {
                    isPressed = true
                    onVibrate()
                    onEvent(Protocol.BUZZ_RED, true)
                    tryAwaitRelease()
                    isPressed = false
                    onEvent(Protocol.BUZZ_RED, false)
                })
            },
        contentAlignment = Alignment.Center
    ) {
        // Base collar
        Box(
            Modifier
                .fillMaxSize()
                .background(Brush.verticalGradient(listOf(Color(0xFF2A2F3A), Color(0xFF10141C))), CircleShape)
                .border(2.dp, Color.White.copy(alpha = 0.08f), CircleShape)
        )
        // Dome
        Box(
            Modifier
                .fillMaxSize(if (isPressed) 0.80f else 0.85f)
                .shadow(if (isPressed) 24.dp else 12.dp, CircleShape, spotColor = AppColors.BuzzRed, ambientColor = AppColors.BuzzRed)
                .background(
                    Brush.radialGradient(
                        colors = listOf(dome.copy(alpha = 1f), Color(0xFF8B0000)),
                        center = Offset(60f, 40f),
                        radius = 260f
                    ),
                    CircleShape
                )
                .border(2.dp, Color.White.copy(alpha = 0.18f), CircleShape)
        )
    }
}

/**
 * One of the four coloured answer buttons below the dome.
 *
 * [label] is for accessibility only — it is the button's contentDescription, not a caption.
 * Printing "BLUE" under a blue button tells a sighted player nothing they cannot see, and
 * four of those turn the bottom of the screen into a row of shouting.
 */
@Composable
fun BuzzAnswerButton(
    label: String,
    color: Color,
    mask: Int,
    onVibrate: () -> Unit,
    onEvent: (Int, Boolean) -> Unit
) {
    var isPressed by remember { mutableStateOf(false) }
    // The hardware's bar, stood on end. Lying flat it could only ever be as long
    // as a quarter of the width; upright it uses the screen's height instead, so
    // four of them fit side by side and each is a far bigger target.
    Box(
        modifier = Modifier
            .semantics { contentDescription = label }
            .size(width = 60.dp, height = 180.dp)
            .shadow(
                if (isPressed) 16.dp else 5.dp,
                RoundedCornerShape(22.dp),
                spotColor = color,
                ambientColor = color
            )
            .clip(RoundedCornerShape(22.dp))
            .background(
                Brush.verticalGradient(
                    if (isPressed) listOf(color, color)
                    else listOf(color, color.copy(alpha = 0.72f))
                )
            )
            .border(2.dp, Color.White.copy(alpha = if (isPressed) 0.45f else 0.14f), RoundedCornerShape(22.dp))
            .pointerInput(mask) {
                detectTapGestures(onPress = {
                    isPressed = true
                    onVibrate()
                    onEvent(mask, true)
                    tryAwaitRelease()
                    isPressed = false
                    onEvent(mask, false)
                })
            },
        contentAlignment = Alignment.Center
    ) {
        // The moulded highlight, running down the button's long side.
        Box(
            Modifier
                .fillMaxHeight(0.82f)
                .width(7.dp)
                .align(Alignment.CenterStart)
                .offset(x = 9.dp)
                .background(Color.White.copy(alpha = 0.22f), CircleShape)
        )
    }
}

@Composable
fun EditorGridBackground(isLight: Boolean) {
    Canvas(Modifier.fillMaxSize()) {
        val lineColor = if (isLight) Color.Black.copy(alpha = 0.08f) else Color.White.copy(alpha = 0.12f)
        val step = 40.dp.toPx()

        for (x in 0..size.width.toInt() step step.toInt()) {
            drawLine(lineColor, Offset(x.toFloat(), 0f), Offset(x.toFloat(), size.height), strokeWidth = 1f)
        }
        for (y in 0..size.height.toInt() step step.toInt()) {
            drawLine(lineColor, Offset(0f, y.toFloat()), Offset(size.width, y.toFloat()), strokeWidth = 1f)
        }
    }
}

@Composable
fun GlobalToast(
    message: String?,
    onDismiss: () -> Unit
) {
    androidx.compose.animation.AnimatedVisibility(
        visible = message != null,
        enter = androidx.compose.animation.fadeIn() + androidx.compose.animation.slideInVertically(initialOffsetY = { -it }),
        exit = androidx.compose.animation.fadeOut() + androidx.compose.animation.slideOutVertically(targetOffsetY = { -it })
    ) {
        val currentMessage = message ?: return@AnimatedVisibility

        LaunchedEffect(currentMessage) {
            kotlinx.coroutines.delay(3000)
            onDismiss()
        }

        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(top = 40.dp)
                .zIndex(2000f),
            contentAlignment = Alignment.TopCenter
        ) {
            Box(
                modifier = Modifier
                    .padding(horizontal = 32.dp)
                    .shadow(12.dp, RoundedCornerShape(24.dp))
                    .background(
                        Brush.horizontalGradient(listOf(Color(0xFF29B6F6), Color(0xFF039BE5))),
                        RoundedCornerShape(24.dp)
                    )
                    .border(1.dp, Color.White.copy(alpha = 0.2f), RoundedCornerShape(24.dp))
                    .padding(horizontal = 24.dp, vertical = 12.dp)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    modifier = Modifier.widthIn(max = 400.dp)
                ) {
                    Icon(
                        Icons.Rounded.Info,
                        null,
                        tint = Color.White,
                        modifier = Modifier.size(22.dp)
                    )
                    Text(
                        text = currentMessage,
                        color = Color.White,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 14.sp,
                        textAlign = androidx.compose.ui.text.style.TextAlign.Start,
                        lineHeight = 18.sp
                    )
                }
            }
        }
    }
}
