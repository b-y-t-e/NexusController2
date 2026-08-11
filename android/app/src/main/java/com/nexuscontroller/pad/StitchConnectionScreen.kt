package com.nexuscontroller.pad

import androidx.compose.animation.core.*
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddLink
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.DesktopWindows
import androidx.compose.material.icons.rounded.QrCodeScanner
import androidx.compose.material.icons.rounded.Usb
import androidx.compose.material.icons.rounded.WifiFind
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.zIndex
import kotlinx.coroutines.delay
import kotlin.math.*

enum class ConnectionState {
    CATEGORIES,
    SCANNING
}

data class FoundDevice(
    val ip: String,
    val name: String,
    val port: Int = Protocol.DEFAULT_PORT,
    val tokenRequired: Boolean = false,
    val lastSeen: Long = System.currentTimeMillis()
)

@Composable
fun StitchConnectionScreen(
    currentIp: String,
    onDismiss: () -> Unit,
    onConnect: (String) -> Unit,
    onConnectDiscovered: (String, Int) -> Unit,
    onQrScan: () -> Unit,
    onStartDiscovery: ((String, DiscoveredServer) -> Unit) -> Unit,
    onStopDiscovery: () -> Unit = {},
    themeMode: String
) {
    var state by remember { mutableStateOf(ConnectionState.CATEGORIES) }
    val foundDevices = remember { mutableStateMapOf<String, FoundDevice>() }

    // Theme Logic
    val isLight = themeMode == "Light"
    val bgColor = when(themeMode) {
         "Light" -> Color.White // Pure White background
         "Neon" -> Color.Black
         else -> AppColors.BackgroundDark
    }
    
    val contentColor = if(isLight) Color(0xFF0D47A1) else Color.White // Dark Blue text
    val subTextColor = if(isLight) Color(0xFF1976D2) else Color.White.copy(alpha=0.6f) // Medium Blue text
    val cardBg = if(isLight) Color.White else AppColors.SurfaceHighlight

    // Use a LaunchedEffect to stop discovery if the whole screen is dismissed
    DisposableEffect(Unit) {
        onDispose {
            onStopDiscovery()
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(bgColor)
            // Block all touches from passing through to layers below
            .pointerInput(Unit) {
                detectTapGestures { /* Swallow */ }
            }
    ) {
        // Carbon Pattern Background (Only for Dark)
        if (themeMode == "Dark") {
            CarbonBackgroundPattern() 
        }

        // Grid Removed

        if (state == ConnectionState.CATEGORIES) {
            CategorySelection(
                onAutoConnect = { 
                    state = ConnectionState.SCANNING 
                    foundDevices.clear()
                    
                    onStartDiscovery { ip, server ->
                        foundDevices[ip] = FoundDevice(ip, server.name, server.port, server.tokenRequired)
                    }
                },
                onUsbConnect = { onConnect("127.0.0.1") },
                onQrScan = onQrScan,
                onDismiss = onDismiss,
                isLight = isLight,
                contentColor = contentColor,
                subTextColor = subTextColor,
                cardBg = cardBg
            )
        } else {
            var showManualIpDialog by remember { mutableStateOf(false) }

            ScanningScreen(
                foundDevices = foundDevices.values.toList(),
                onBack = { 
                    state = ConnectionState.CATEGORIES 
                    onStopDiscovery()
                    foundDevices.clear()
                },
                onConnect = onConnectDiscovered,
                onManualIp = { showManualIpDialog = true },
                isLight = isLight,
                contentColor = contentColor,
                subTextColor = subTextColor,
                cardBg = cardBg
            )

            if (showManualIpDialog) {
                ManualIpDialog(
                    onDismiss = { showManualIpDialog = false },
                    onConnect = { ip ->
                        showManualIpDialog = false
                        onConnect(ip)
                    },
                    isLight = isLight,
                    contentColor = contentColor,
                    cardBg = cardBg
                )
            }
        }
    }
}

@Composable
fun CategorySelection(
    onAutoConnect: () -> Unit,
    onUsbConnect: () -> Unit,
    onQrScan: () -> Unit,
    onDismiss: () -> Unit,
    isLight: Boolean,
    contentColor: Color,
    subTextColor: Color,
    cardBg: Color
) {
    val accentBlue = Color(0xFF2979FF) // Bright Blue
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                "SELECT INTERFACE",
                color = subTextColor,
                fontSize = 12.sp,
                fontWeight = FontWeight.Black,
                letterSpacing = 4.sp
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                "CONNECT TO HOST",
                color = contentColor,
                fontSize = 32.sp,
                fontWeight = FontWeight.Black,
                letterSpacing = 2.sp
            )
            Spacer(modifier = Modifier.height(48.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(32.dp)) {
                ConnectionOptionCard(
                    title = "AUTO SCAN",
                    subtitle = "NETWORK RADAR",
                    icon = Icons.Rounded.WifiFind,
                    color = accentBlue,
                    onClick = onAutoConnect,
                    cardBg = cardBg,
                    contentColor = contentColor,
                    subTextColor = subTextColor
                )
                ConnectionOptionCard(
                    title = "USB DIRECT",
                    subtitle = "WIRED INTERFACE",
                    icon = Icons.Rounded.Usb,
                    color = accentBlue,
                    onClick = onUsbConnect,
                    cardBg = cardBg,
                    contentColor = contentColor,
                    subTextColor = subTextColor
                )
                ConnectionOptionCard(
                    title = "QR SCAN",
                    subtitle = "INSTANT SYNC",
                    icon = Icons.Rounded.QrCodeScanner,
                    color = accentBlue,
                    onClick = onQrScan,
                    cardBg = cardBg,
                    contentColor = contentColor,
                    subTextColor = subTextColor
                )
            }
        }

        IconButton(
            onClick = onDismiss,
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(32.dp)
                .size(48.dp)
                .background(if(isLight) Color.Black.copy(alpha=0.05f) else Color.White.copy(alpha=0.05f), CircleShape)
                .border(1.dp, if(isLight) Color.Black.copy(alpha=0.1f) else Color.White.copy(alpha=0.1f), CircleShape)
        ) {
            Icon(Icons.Default.Close, "Close", tint = contentColor.copy(alpha=0.6f))
        }
    }
}

@Composable
fun ScanningScreen(
    foundDevices: List<FoundDevice>,
    onBack: () -> Unit,
    onConnect: (String, Int) -> Unit,
    onManualIp: () -> Unit,
    isLight: Boolean,
    contentColor: Color,
    subTextColor: Color,
    cardBg: Color
) {
    val accentBlue = Color(0xFF2979FF)
    
    Box(modifier = Modifier.fillMaxSize()) {
        // Main Content Area
        Row(modifier = Modifier.fillMaxSize()) {
            // Left: Immersive Radar
            Box(
                modifier = Modifier
                    .weight(1.8f)
                    .fillMaxHeight(),
                contentAlignment = Alignment.Center
            ) {
                RadarView(accentBlue)
                
                // Found Device Indicators
                foundDevices.forEachIndexed { index, device ->
                    val angle = (index * 137.5f) % 360f
                    val dist = 0.3f + (index % 3) * 0.2f
                    DeviceDot(angle, dist, device.name, accentBlue)
                }

            }

            // Right: Device List with Glassmorphic design
            Column(
                modifier = Modifier
                    .width(320.dp)
                    .fillMaxHeight()
                    .then(
                        if (isLight) {
                             Modifier.background(Color.White.copy(alpha=0.95f))
                        } else {
                             Modifier.background(
                                Brush.horizontalGradient(
                                    listOf(Color.Black.copy(alpha = 0.2f), Color.Black.copy(alpha = 0.8f))
                                )
                             )
                        }
                    )
                    .border(1.dp, contentColor.copy(alpha = 0.05f))
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 16.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text("DEVICES", color = contentColor, fontWeight = FontWeight.Black, fontSize = 14.sp, letterSpacing = 2.sp)
                        Text("DETECTED IN RADAR", color = subTextColor, fontSize = 8.sp, fontWeight = FontWeight.Bold)
                    }
                    
                    if (foundDevices.isNotEmpty()) {
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(50))
                                .background(accentBlue.copy(alpha = 0.1f))
                                .border(1.dp, accentBlue.copy(alpha = 0.3f), RoundedCornerShape(50))
                                .padding(horizontal = 10.dp, vertical = 4.dp)
                        ) {
                            Text(
                                "${foundDevices.size} ACTIVE",
                                color = accentBlue,
                                fontSize = 9.sp,
                                fontWeight = FontWeight.Black
                            )
                        }
                    }
                }
                
                Canvas(Modifier.fillMaxWidth().height(1.dp).padding(horizontal = 24.dp)) {
                    drawLine(contentColor.copy(alpha=0.05f), Offset.Zero, Offset(size.width, 0f))
                }
                
                LazyColumn(
                    modifier = Modifier.weight(1f).padding(horizontal = 20.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    items(items = foundDevices, key = { it.ip }) { device ->
                        DeviceItem(device, accentBlue, onConnect, isLight, contentColor, subTextColor)
                    }
                }
                
                // Manual IP Box
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 20.dp, start = 20.dp, end = 20.dp, top = 8.dp)
                ) {
                    Button(
                        onClick = onManualIp,
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(42.dp)
                            .clip(RoundedCornerShape(12.dp)),
                        colors = ButtonDefaults.buttonColors(containerColor = contentColor.copy(alpha = 0.05f)),
                        shape = RoundedCornerShape(12.dp),
                        border = BorderStroke(1.dp, contentColor.copy(alpha = 0.03f)),
                        elevation = ButtonDefaults.buttonElevation(
                            defaultElevation = 0.dp,
                            pressedElevation = 0.dp,
                            focusedElevation = 0.dp,
                            hoveredElevation = 0.dp
                        )
                    ) {
                        Icon(Icons.Default.AddLink, null, tint = contentColor.copy(alpha=0.4f), modifier = Modifier.size(16.dp))
                        Spacer(modifier = Modifier.width(8.dp))
                        Text("MANUAL IP OVERRIDE", color = contentColor.copy(alpha=0.6f), fontWeight = FontWeight.Bold, fontSize = 10.sp)
                    }
                }
            }
        }

        // Floating Back Button (Integrated)
        IconButton(
            onClick = onBack,
            modifier = Modifier
                .padding(24.dp)
                .size(48.dp)
                .shadow(if(isLight) 4.dp else 0.dp, CircleShape)
                .clip(CircleShape)
                .background(if(isLight) Color.White else AppColors.Surface) // Opaque
                .border(1.dp, if(isLight) Color(0xFFE0E0E0) else Color.White.copy(alpha = 0.1f), CircleShape)
        ) {
            Icon(Icons.Default.ArrowBack, "Back", tint = if(isLight) Color.Black else Color.White.copy(alpha=0.8f))
        }

    }
}

@Composable
fun RadarView(accentColor: Color) {
    val infiniteTransition = rememberInfiniteTransition()
    val rotation by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(
            animation = tween(4000, easing = LinearEasing),
            repeatMode = RepeatMode.Restart
        )
    )

    Box(modifier = Modifier.size(420.dp), contentAlignment = Alignment.Center) {
        Canvas(modifier = Modifier.fillMaxSize()) {
            val center = Offset(size.width / 2, size.height / 2)
            
            val radius = min(size.width, size.height) * 0.45f

            // Circles
            drawCircle(accentColor.copy(alpha = 0.15f), radius = radius, style = Stroke(1f))
            drawCircle(accentColor.copy(alpha = 0.08f), radius = radius * 0.66f, style = Stroke(1f, pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 10f))))
            drawCircle(accentColor.copy(alpha = 0.12f), radius = radius * 0.33f, style = Stroke(1f))
            drawCircle(accentColor.copy(alpha = 0.25f), radius = radius * 0.1f, style = Stroke(2f))

            // Crosshair
            drawLine(accentColor.copy(alpha = 0.15f), Offset(center.x - radius, center.y), Offset(center.x + radius, center.y))
            drawLine(accentColor.copy(alpha = 0.15f), Offset(center.x, center.y - radius), Offset(center.x, center.y + radius))
            
            // Rotating Sweep
            withTransform({
                rotate(rotation, center)
            }) {
                drawCircle(
                    brush = Brush.sweepGradient(
                        0f to Color.Transparent,
                        0.75f to Color.Transparent,
                        1f to accentColor.copy(alpha = 0.4f),
                        center = center
                    ),
                    radius = radius,
                    center = center
                )
            }
        }
        
        // Center Dot
        Box(modifier = Modifier.size(16.dp).background(accentColor, CircleShape).border(2.dp, Color.White.copy(alpha = 0.2f), CircleShape))
    }
}

@Composable
fun DeviceDot(angle: Float, distFactor: Float, label: String, accentColor: Color) {
    Box(modifier = Modifier.fillMaxSize()) {
        val infiniteTransition = rememberInfiniteTransition()
        val opacity by infiniteTransition.animateFloat(
            initialValue = 0.5f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(
                animation = tween(2000),
                repeatMode = RepeatMode.Reverse
            )
        )

        Box(
            modifier = Modifier
                .align(Alignment.Center)
                .offset(
                    x = (200 * distFactor * cos(Math.toRadians(angle.toDouble()))).dp,
                    y = (200 * distFactor * sin(Math.toRadians(angle.toDouble()))).dp
                )
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Box(
                    modifier = Modifier
                        .size(10.dp)
                        .alpha(opacity)
                        .background(Color.White, CircleShape)
                        .border(2.dp, accentColor, CircleShape)
                )
                Surface(
                    modifier = Modifier.padding(top = 6.dp),
                    color = Color.Black.copy(alpha = 0.85f),
                    shape = RoundedCornerShape(4.dp),
                    border = BorderStroke(1.dp, accentColor.copy(alpha = 0.4f))
                ) {
                    Text(
                        label.uppercase(),
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        color = Color.White,
                        fontSize = 8.sp,
                        fontWeight = FontWeight.Black,
                        letterSpacing = 1.sp
                    )
                }
            }
        }
    }
}

@Composable
fun DeviceItem(
    device: FoundDevice,
    accentColor: Color,
    onConnect: (String, Int) -> Unit,
    isLight: Boolean,
    contentColor: Color,
    subTextColor: Color
) {
    Surface(
        color = if(isLight) Color.Black.copy(alpha=0.03f) else Color.White.copy(alpha = 0.05f),
        shape = RoundedCornerShape(12.dp),
        border = BorderStroke(1.dp, contentColor.copy(alpha = 0.05f)),
        modifier = Modifier.fillMaxWidth()
    ) {
        Row(
            modifier = Modifier.padding(10.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .size(32.dp)
                    .background(contentColor.copy(alpha = 0.1f), RoundedCornerShape(8.dp))
                    .border(1.dp, contentColor.copy(alpha = 0.08f), RoundedCornerShape(8.dp)),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    if (device.name.contains("DESKTOP", true)) Icons.Default.DesktopWindows else Icons.Default.Computer,
                    null,
                    tint = contentColor.copy(alpha=0.6f),
                    modifier = Modifier.size(16.dp)
                )
            }
            Spacer(modifier = Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(device.name.uppercase(), color = contentColor, fontWeight = FontWeight.Black, fontSize = 11.sp, letterSpacing = 1.sp, maxLines = 1)
                Text(
                    "${device.ip}:${device.port}${if (device.tokenRequired) "  • PAIRING CODE" else ""}",
                    color = subTextColor,
                    fontSize = 10.sp,
                    fontFamily = FontFamily.Monospace
                )
            }
            Button(
                onClick = { onConnect(device.ip, device.port) },
                colors = ButtonDefaults.buttonColors(containerColor = accentColor),
                shape = RoundedCornerShape(8.dp),
                contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                modifier = Modifier.height(30.dp)
            ) {
                Text("CONNECT", fontSize = 9.sp, fontWeight = FontWeight.Black, color = Color.White)
            }
        }
    }
}

@Composable
fun ConnectionOptionCard(
    title: String, 
    subtitle: String, 
    icon: ImageVector, 
    color: Color, 
    onClick: () -> Unit,
    cardBg: Color,
    contentColor: Color,
    subTextColor: Color
) {
    Box(
        modifier = Modifier
            .size(180.dp, 160.dp)
            .clip(RoundedCornerShape(24.dp))
            .background(cardBg)
            .border(1.dp, contentColor.copy(alpha = 0.05f), RoundedCornerShape(24.dp))
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Box(
                modifier = Modifier
                    .size(56.dp)
                    .background(contentColor.copy(alpha = 0.05f), CircleShape)
                    .border(1.dp, contentColor.copy(alpha = 0.1f), CircleShape),
                contentAlignment = Alignment.Center
            ) {
                Icon(icon, null, tint = color, modifier = Modifier.size(28.dp))
            }
            Spacer(modifier = Modifier.height(16.dp))
            Text(title, color = contentColor, fontSize = 12.sp, fontWeight = FontWeight.Bold)
            Text(subtitle, color = subTextColor, fontSize = 10.sp)
        }
    }
}

@Composable
fun ManualIpDialog(
    onDismiss: () -> Unit,
    onConnect: (String) -> Unit,
    isLight: Boolean,
    contentColor: Color,
    cardBg: Color
) {
    var ipText by remember { mutableStateOf("") }
    val isValid = QrPayload.parse(ipText) != null

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = cardBg,
        title = {
            Text("Enter Host IP", color = contentColor, fontWeight = FontWeight.Bold)
        },
        text = {
            OutlinedTextField(
                value = ipText,
                onValueChange = { ipText = it },
                label = { Text("IP Address") },
                isError = ipText.isNotEmpty() && !isValid,
                supportingText = {
                    if (ipText.isNotEmpty() && !isValid) {
                        Text("Enter an IPv4 address, e.g. 192.168.1.20", color = Color(0xFFEF4444), fontSize = 11.sp)
                    }
                },
                singleLine = true,
                colors = OutlinedTextFieldDefaults.colors(
                    focusedTextColor = contentColor,
                    unfocusedTextColor = contentColor,
                    focusedBorderColor = Color(0xFF2979FF),
                    unfocusedBorderColor = contentColor.copy(alpha = 0.2f),
                    focusedLabelColor = Color(0xFF2979FF),
                    unfocusedLabelColor = contentColor.copy(alpha = 0.6f)
                ),
                modifier = Modifier.fillMaxWidth()
            )
        },
        confirmButton = {
            Button(
                enabled = isValid,
                onClick = { if (isValid) onConnect(ipText) },
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF2979FF))
            ) {
                Text("Connect", color = Color.White)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Cancel", color = contentColor.copy(alpha = 0.6f))
            }
        }
    )
}
