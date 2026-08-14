package com.nexuscontroller.pad

import androidx.compose.animation.core.*
import androidx.compose.foundation.Image
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.*
import androidx.compose.material.icons.outlined.Security
import androidx.compose.material.icons.rounded.ArrowBack
import androidx.compose.material.icons.rounded.Groups
import androidx.compose.material.icons.rounded.RocketLaunch
import androidx.compose.material.icons.rounded.Gavel
import androidx.compose.material.icons.rounded.OpenInNew
import androidx.compose.material.icons.rounded.Update
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex

@Composable
fun AboutScreen(
    onBack: () -> Unit,
    themeMode: String,
    updates: UpdateController
) {
    val isLight = themeMode == "Light"

    // Updating belongs to the activity now, not to this screen: the app checks
    // once when it starts, the main menu offers what it finds, and a download
    // outlives the screen it was started from. This one only reads the state and
    // taps the same single decision — see UpdateController.act().
    val updateStatus = updates.status

    // Theme Colors matching the HTML/Tailwind design
    val primaryColor = Color(0xFF0d59f2) // "primary": "#0d59f2"
    val backgroundDark = Color(0xFF0a0e14) // "background-dark": "#0a0e14"
    val surfaceDark = Color(0xFF151921)    // "surface-dark": "#151921"
    
    val bgColor = if(isLight) AppColors.BackgroundLight else backgroundDark
    val surfaceColor = if(isLight) Color.White else surfaceDark
    val textColor = if(isLight) Color(0xFF1F2937) else Color.White
    val subTextColor = if(isLight) Color(0xFF6B7280) else Color(0xFF9CA3AF) // Gray-400 equivalent
    val borderColor = if(isLight) Color(0xFFE5E7EB) else Color.White.copy(alpha = 0.05f)

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(bgColor)
    ) {
        // Background Patterns
        if (!isLight) {
            CarbonBackgroundPattern()
            // Grid Overlay (Opacity 0.5 in HTML)
            GridBackground(Color(0xFF0d59f2).copy(alpha = 0.05f))
        }

        // Content Layer (Scrollable, Full Screen)
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp), // Removed vertical padding, handled by spacers
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
             // Spacer for Header (Back button area)
             Spacer(modifier = Modifier.height(80.dp))

            // Hero Section
            Box(
                modifier = Modifier.padding(bottom = 40.dp),
                contentAlignment = Alignment.Center
            ) {
                // Glow Effect
                Box(
                    modifier = Modifier
                        .size(120.dp)
                        .blur(40.dp)
                        .background(primaryColor.copy(alpha = 0.2f), CircleShape)
                )
                
                // Icon Container
                Box(
                    modifier = Modifier
                        .size(80.dp)
                        .background(
                            Brush.linearGradient(
                                colors = listOf(primaryColor, Color(0xFF9333EA)) // to purple-600
                            ),
                            RoundedCornerShape(16.dp)
                        )
                        .shadow(25.dp, spotColor = primaryColor.copy(alpha = 0.6f)),
                    contentAlignment = Alignment.Center
                ) {
                    Image(
                        painter = androidx.compose.ui.res.painterResource(id = R.drawable.logo),
                        contentDescription = "Nexus Controller Logo",
                        modifier = Modifier.size(56.dp)
                    )
                }
            }
            
            // Title
            Text(
                buildAnnotatedString {
                    append("NEXUS ")
                    withStyle(SpanStyle(color = primaryColor)) {
                        append("CONTROLLER")
                    }
                },
                fontSize = 32.sp,
                fontWeight = FontWeight.Bold,
                color = textColor,
                letterSpacing = 1.sp, 
                modifier = Modifier.padding(bottom = 4.dp)
            )
            
            // Version
            Text(
                buildAnnotatedString {
                    append("VERSION ")
                    withStyle(SpanStyle(color = textColor)) {
                        // From the build, not from a literal. This said "2.4.0
                        // PRO" while the app was 2.0 — harmless as decoration,
                        // but it is now the number the update check compares
                        // against, and a wrong one either hides a real update or
                        // offers one forever.
                        append(BuildConfig.VERSION_NAME)
                    }
                },
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                color = subTextColor,
                letterSpacing = 3.sp
            )
            
            Spacer(modifier = Modifier.height(40.dp))
            
            // --- SECTIONS ---
            Column(
                verticalArrangement = Arrangement.spacedBy(24.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                // What it is, in one honest paragraph. The original said this
                // was "ultimate low-latency remote play" bridging "professional-
                // grade performance", which describes a streaming product rather
                // than this one.
                AboutCard(
                    title = stringResource(R.string.about_title),
                    icon = Icons.Rounded.RocketLaunch,
                    primaryColor = primaryColor,
                    surfaceColor = surfaceColor,
                    borderColor = borderColor
                ) {
                    Text(
                        stringResource(R.string.about_tagline),
                        color = subTextColor,
                        fontSize = 14.sp,
                        lineHeight = 22.sp
                    )
                }

                AboutCard(
                    title = stringResource(R.string.about_how_title),
                    icon = Icons.Rounded.Groups,
                    primaryColor = primaryColor,
                    surfaceColor = surfaceColor,
                    borderColor = borderColor
                ) {
                    // The "Development" card credited Alex Rivers, Marcus Chen and
                    // Sarah Jenkins — none of whom exist. Invented credits in a
                    // shipped app are a lie, so this says how the thing works.
                    Text(
                        stringResource(R.string.about_how_body),
                        color = subTextColor,
                        fontSize = 14.sp,
                        lineHeight = 22.sp
                    )
                }

                AboutCard(
                    title = stringResource(R.string.about_privacy_title),
                    icon = Icons.Rounded.Gavel,
                    primaryColor = primaryColor,
                    surfaceColor = surfaceColor,
                    borderColor = borderColor
                ) {
                    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text(
                            stringResource(R.string.about_privacy_body),
                            color = subTextColor,
                            fontSize = 14.sp,
                            lineHeight = 22.sp
                        )
                        Text(
                            stringResource(R.string.about_source_body),
                            color = subTextColor,
                            fontSize = 14.sp,
                            lineHeight = 22.sp
                        )
                    }
                }
            }
            
            Spacer(modifier = Modifier.height(32.dp))
            
            // --- UPDATE BUTTON ---
            val rotation = remember { Animatable(0f) }
            LaunchedEffect(Unit) {
                rotation.animateTo(
                    targetValue = 360f,
                    animationSpec = infiniteRepeatable(
                        animation = tween(8000, easing = LinearEasing),
                        repeatMode = RepeatMode.Restart
                    )
                )
            }
            
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(50))
                    .background(primaryColor)
                    // Was an empty onClick under the words "CHECK FOR UPDATES" —
                    // a button that promised to do something and did nothing.
                    // It now does it: checks, and once there is something to
                    // install, installs it.
                    // Check, ask for the install permission, or install: one
                    // decision, written once, in UpdateController.act() — the
                    // entry in the main menu presses the same one.
                    .clickable(enabled = updateStatus.isActionable()) { updates.act() }
                    .padding(horizontal = 40.dp, vertical = 16.dp)
                    .shadow(15.dp, spotColor = primaryColor.copy(alpha = 0.4f)) // Neon Shadow
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Icon(
                        Icons.Rounded.Update, 
                        null, 
                        tint = Color.White, 
                        modifier = Modifier.size(20.dp).rotate(rotation.value)
                    )
                    Text(
                        when (val status = updateStatus) {
                            is UpdateStatus.Checking -> stringResource(R.string.update_checking)
                            is UpdateStatus.Available ->
                                stringResource(R.string.update_install, status.release.version)
                            is UpdateStatus.Downloading ->
                                stringResource(R.string.update_downloading, status.percent)
                            is UpdateStatus.Installing -> stringResource(R.string.update_installing)
                            else -> stringResource(R.string.update_check)
                        },
                        color = Color.White,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = 2.sp
                    )
                }
            }

            // What the button cannot say: why there is nothing to install, or why
            // the last attempt did not work. Silent when there is no news.
            val note = when (val status = updateStatus) {
                is UpdateStatus.UpToDate -> stringResource(R.string.update_up_to_date)
                is UpdateStatus.Installed -> stringResource(R.string.update_installed)
                is UpdateStatus.Failed -> status.message
                is UpdateStatus.Available ->
                    if (updates.canInstallPackages()) null
                    else stringResource(R.string.update_needs_permission)
                else -> null
            }
            if (note != null) {
                Spacer(modifier = Modifier.height(12.dp))
                Text(
                    note,
                    color = subTextColor,
                    fontSize = 12.sp,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(horizontal = 24.dp)
                )
            }

            Spacer(modifier = Modifier.height(12.dp))
            Text(
                stringResource(R.string.about_releases),
                color = subTextColor,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 1.sp,
                modifier = Modifier
                    .clickable { updates.openReleasesPage() }
                    .padding(8.dp)
            )
            
            Spacer(modifier = Modifier.height(16.dp))
            
            Text(
                "© 2024 Nexus Systems Global. All rights reserved.",
                color = subTextColor.copy(alpha = 0.8f),
                fontSize = 10.sp,
                fontWeight = FontWeight.Medium
            )
            
            Spacer(modifier = Modifier.height(32.dp))
            
            // Home Indicator
            Box(
                modifier = Modifier
                    .width(128.dp)
                    .height(4.dp)
                    .background(Color.White.copy(alpha = 0.2f), CircleShape)
            )
            
            Spacer(modifier = Modifier.height(16.dp))
        }

        // Header Layer (Floating on top)
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .align(Alignment.TopStart) // Key change
        ) {
             Row(
                 modifier = Modifier.padding(horizontal = 24.dp, vertical = 16.dp),
                 verticalAlignment = Alignment.CenterVertically,
                 horizontalArrangement = Arrangement.Start
             ) {
                 // Back Button
                 Box(
                    modifier = Modifier
                        .size(40.dp)
                        .clip(CircleShape)
                        .background(if(isLight) Color.White else Color(0xFF151921)) // Surface color
                        .clickable { onBack() }
                        .border(1.dp, borderColor, CircleShape),
                    contentAlignment = Alignment.Center
                 ) {
                     Icon(Icons.Rounded.ArrowBack, contentDescription = "Back", tint = if(isLight) textColor else Color.White)
                 }
             }
        }
    }
}

@Composable
fun AboutCard(
    title: String,
    icon: ImageVector,
    primaryColor: Color,
    surfaceColor: Color,
    borderColor: Color,
    content: @Composable () -> Unit
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(surfaceColor.copy(alpha = 0.4f)) // surface-dark/40
            .border(1.dp, borderColor, RoundedCornerShape(16.dp))
            .padding(24.dp)
    ) {
        Column {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.padding(bottom = 16.dp)
            ) {
                Icon(icon, null, tint = primaryColor)
                Text(
                    title.uppercase(),
                    color = Color(0xFFD1D5DB), // Gray 300
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.sp
                )
            }
            content()
        }
    }
}

@Composable
fun DevMember(role: String, name: String, subColor: Color, textColor: Color) {
    Column {
        Text(
            role.uppercase(),
            color = subColor,
            fontSize = 12.sp,
            fontWeight = FontWeight.Bold,
            letterSpacing = 0.5.sp
        )
        Text(
            name,
            color = textColor.copy(alpha = 0.9f), // Gray 200 equivalentish
            fontSize = 14.sp
        )
    }
}

@Composable
fun LegalLink(
    text: String, 
    color: Color, 
    borderColor: Color, 
    isFirst: Boolean,
    dummyContent: String
) {
    var expanded by remember { mutableStateOf(false) }
    val rotation by animateFloatAsState(targetValue = if (expanded) 180f else 0f)

    Column {
        if (!isFirst) {
            Box(Modifier.fillMaxWidth().height(1.dp).background(borderColor).padding(vertical = 8.dp))
            Spacer(modifier = Modifier.height(8.dp))
        }
        
        // Header Row
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { expanded = !expanded }
                .padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(text, color = color, fontSize = 14.sp)
            Icon(
                Icons.Rounded.KeyboardArrowDown, 
                contentDescription = "Expand", 
                tint = color.copy(alpha = 0.7f),
                modifier = Modifier.rotate(rotation)
            )
        }
        
        // Expandable Content
        androidx.compose.animation.AnimatedVisibility(visible = expanded) {
             Column(modifier = Modifier.padding(bottom = 12.dp)) {
                 Text(
                     text = dummyContent,
                     color = color.copy(alpha = 0.7f),
                     fontSize = 12.sp,
                     lineHeight = 18.sp
                 )
             }
        }
        
        if (isFirst) Spacer(modifier = Modifier.height(8.dp))
    }
}

@Composable
fun GridBackground(gridColor: Color) {
    Canvas(modifier = Modifier.fillMaxSize()) {
        val gridSize = 40.dp.toPx()
        val strokeWidth = 1.dp.toPx()
        
        // Vertical lines
        for (x in 0..size.width.toInt() step gridSize.toInt()) {
            drawLine(
                color = gridColor,
                start = Offset(x.toFloat(), 0f),
                end = Offset(x.toFloat(), size.height),
                strokeWidth = strokeWidth
            )
        }
        
        // Horizontal lines
        for (y in 0..size.height.toInt() step gridSize.toInt()) {
            drawLine(
                color = gridColor,
                start = Offset(0f, y.toFloat()),
                end = Offset(size.width, y.toFloat()),
                strokeWidth = strokeWidth
            )
        }
    }
}
