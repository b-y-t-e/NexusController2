package com.nexuscontroller.pad

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.SportsEsports
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun RacingPedal(
    label: String,
    modifier: Modifier = Modifier,
    color: Color = AppColors.NeonBlue,
    width: Dp = 80.dp,
    onValueChange: (Float) -> Unit
) {
    var isPressed by remember { mutableStateOf(false) }
    val pressure by animateFloatAsState(if (isPressed) 1f else 0f, label = "pressure", animationSpec = spring(stiffness = Spring.StiffnessLow))

    Column(
        modifier = modifier
            .width(width)
            .pointerInput(Unit) {
                awaitEachGesture {
                    awaitFirstDown()
                    isPressed = true
                    do {
                        val event = awaitPointerEvent()
                    } while (event.changes.any { it.pressed })
                    isPressed = false
                }
            },
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
                .graphicsLayer {
                    translationY = pressure * 20f
                    rotationX = -pressure * 10f
                    scaleX = 1f - (pressure * 0.05f)
                }
                .shadow(if (isPressed) 20.dp else 4.dp, RoundedCornerShape(topStart = 4.dp, topEnd = 4.dp, bottomStart = 12.dp, bottomEnd = 12.dp), spotColor = color, ambientColor = color)
                .background(
                    brush = Brush.verticalGradient(colors = listOf(Color(0xFF444444), Color(0xFF1A1A1A))),
                    shape = RoundedCornerShape(topStart = 4.dp, topEnd = 4.dp, bottomStart = 12.dp, bottomEnd = 12.dp)
                )
                .border(1.dp, Color.White.copy(alpha = 0.1f), RoundedCornerShape(topStart = 4.dp, topEnd = 4.dp, bottomStart = 12.dp, bottomEnd = 12.dp)),
            contentAlignment = Alignment.Center
        ) {
            Canvas(Modifier.fillMaxSize()) {
                repeat(10) { i ->
                    drawLine(Color.White.copy(alpha = 0.03f), Offset(0f, size.height * i / 10f), Offset(size.width, size.height * i / 10f), strokeWidth = 1f)
                }
            }

            Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                repeat(4) {
                    Box(
                        Modifier.size(8.dp).background(
                            brush = Brush.linearGradient(listOf(Color.Black, Color(0xFF333333))),
                            shape = CircleShape
                        ).border(1.dp, Color.White.copy(alpha = 0.1f), CircleShape)
                    )
                }
            }
        }

        Spacer(Modifier.height(12.dp))
        Text(
            label.uppercase(),
            color = if (isPressed) color else Color.Gray,
            fontSize = 10.sp,
            fontWeight = FontWeight.Black,
            letterSpacing = 2.sp
        )

        Spacer(Modifier.height(4.dp))
        Box(Modifier.fillMaxWidth(0.8f).height(2.dp).background(Color.White.copy(alpha = 0.05f), CircleShape)) {
            Box(Modifier.fillMaxWidth(pressure).fillMaxHeight().background(color, CircleShape).shadow(4.dp, spotColor = color, ambientColor = color))
        }
    }

    LaunchedEffect(isPressed) {
        onValueChange(if (isPressed) 1f else 0f)
    }
}

@Composable
fun RacingDashboard(speed: Int, gear: Int) {
    Box(
        modifier = Modifier
            .width(220.dp)
            .height(280.dp)
            .animateContentSize()
            .background(Color(0xFF0A0A0A).copy(alpha = 0.8f), RoundedCornerShape(32.dp))
            .border(1.dp, Color.White.copy(alpha = 0.05f), RoundedCornerShape(32.dp))
            .padding(2.dp)
    ) {
        Box(
            Modifier.fillMaxWidth().height(100.dp).background(
                Brush.verticalGradient(listOf(Color.White.copy(alpha = 0.05f), Color.Transparent)),
                RoundedCornerShape(topStart = 32.dp, topEnd = 32.dp)
            )
        )

        Column(Modifier.padding(20.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Row(
                Modifier.fillMaxWidth().height(30.dp),
                horizontalArrangement = Arrangement.spacedBy(3.dp),
                verticalAlignment = Alignment.Bottom
            ) {
                repeat(10) { i ->
                    val color = when {
                        i < 6 -> Color(0xFF22C55E)
                        i < 8 -> Color(0xFFFBBF24)
                        else -> Color(0xFFEF4444)
                    }
                    val isActive = speed > (i * 18)

                    Box(
                        Modifier
                            .weight(1f)
                            .fillMaxHeight(0.3f + (i * 0.07f))
                            .clip(RoundedCornerShape(2.dp))
                            .background(color.copy(alpha = if (isActive) 1f else 0.1f))
                    ) {
                        if (isActive) {
                            Box(Modifier.fillMaxSize().shadow(10.dp, spotColor = color, ambientColor = color))
                        }
                    }
                }
            }

            Spacer(Modifier.weight(1f))

            Box(
                modifier = Modifier
                    .size(100.dp)
                    .background(Color(0xFF111111), RoundedCornerShape(16.dp))
                    .border(1.dp, Color.White.copy(alpha = 0.05f), RoundedCornerShape(16.dp)),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = gear.toString(),
                    fontSize = 64.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color.White,
                    fontFamily = FontFamily.Monospace
                )
                Text(
                    stringResource(R.string.racing_gear),
                    Modifier.align(Alignment.TopCenter).padding(top = 8.dp),
                    fontSize = 9.sp,
                    color = Color.Gray,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 2.sp
                )
            }

            Spacer(Modifier.height(16.dp))

            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    speed.toString(),
                    fontSize = 56.sp,
                    fontWeight = FontWeight.Black,
                    color = Color.White,
                    letterSpacing = (-2).sp
                )
                Text("KM/H", fontSize = 10.sp, fontWeight = FontWeight.Black, color = AppColors.NeonBlue, letterSpacing = 2.sp)
            }

            Spacer(Modifier.height(12.dp))

            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
                Text("ABS", fontSize = 9.sp, fontWeight = FontWeight.Bold, color = Color.DarkGray)
                Text("TCS", fontSize = 9.sp, fontWeight = FontWeight.Bold, color = AppColors.NeonBlue)
            }
        }
    }
}

@Composable
fun RacingWheel(rotation: Float, onRotate: (Float) -> Unit) {
    Box(
        modifier = Modifier.size(320.dp),
        contentAlignment = Alignment.Center
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(Unit) {
                    detectDragGestures { change, dragAmount ->
                        onRotate((rotation + dragAmount.x * 0.4f).coerceIn(-135f, 135f))
                        change.consume()
                    }
                }
        )

        Box(
            modifier = Modifier
                .size(320.dp)
                .rotate(rotation),
            contentAlignment = Alignment.Center
        ) {
            Canvas(Modifier.fillMaxSize()) {
                drawCircle(Color.Black, radius = size.width / 2, style = Stroke(width = 24.dp.toPx()))
                drawCircle(
                    brush = Brush.radialGradient(listOf(Color(0xFF2A2A2A), Color.Black)),
                    radius = size.width / 2,
                    style = Stroke(width = 20.dp.toPx())
                )
                drawCircle(Color.White.copy(alpha = 0.05f), radius = (size.width / 2), style = Stroke(width = 1.dp.toPx()))
            }

            Box(
                modifier = Modifier
                    .size(220.dp)
                    .clip(CircleShape)
                    .background(Color(0xFF111111))
                    .border(1.dp, Color.White.copy(alpha = 0.05f), CircleShape),
                contentAlignment = Alignment.Center
            ) {
                Box(Modifier.fillMaxWidth().height(12.dp).background(Color(0xFF222222)))
                Box(Modifier.fillMaxHeight().width(12.dp).background(Color(0xFF222222)))

                Box(
                    modifier = Modifier
                        .size(70.dp)
                        .background(
                            brush = Brush.linearGradient(listOf(Color(0xFF333333), Color.Black)),
                            shape = CircleShape
                        )
                        .border(1.dp, Color.White.copy(alpha = 0.1f), CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Rounded.SportsEsports,
                        null,
                        tint = AppColors.NeonBlue.copy(alpha = 0.8f),
                        modifier = Modifier.size(32.dp)
                    )
                }
            }

            Box(
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .offset(y = 4.dp)
                    .size(24.dp, 12.dp)
                    .background(AppColors.NeonBlue, RoundedCornerShape(4.dp))
                    .shadow(15.dp, spotColor = AppColors.NeonBlue, ambientColor = AppColors.NeonBlue)
            )
        }
    }
}
