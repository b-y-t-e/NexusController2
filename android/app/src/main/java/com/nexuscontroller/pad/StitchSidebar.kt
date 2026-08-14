package com.nexuscontroller.pad

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.*
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex

@Composable
fun StitchSidebar(
    isVisible: Boolean,
    currentMode: Int,
    controllerType: ControllerType,
    onControllerTypeChange: (ControllerType) -> Unit,
    onModeSelect: (Int) -> Unit,
    isConnected: Boolean,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
    onDismiss: () -> Unit,
    onSettingsClick: () -> Unit,
    onHelpClick: () -> Unit, 
    onAboutClick: () -> Unit,
    onLayoutsClick: () -> Unit, // Added for Custom Layouts
    themeMode: String,
    /** Null while there is nothing to offer — see the entry below. */
    updateLabel: String? = null,
    onUpdateClick: () -> Unit = {}
) {
    val accentPrimary = Color(0xFF0d59f2)
    val backgroundDark = Color(0xFF151921)
    
    val bgColor = when(themeMode) {
        "Light" -> AppColors.BackgroundLight
        "Neon" -> Color.Black
        else -> backgroundDark
    }
    
    val contentColor = if(themeMode == "Light") Color(0xFF333333) else Color.Gray
    val titleColor = if(themeMode == "Light") Color.Black else Color.White
    val borderColor = if(themeMode == "Light") Color(0xFFCCCCCC) else Color.White.copy(alpha = 0.1f)
    
    AnimatedVisibility(
        visible = isVisible,
        enter = slideInHorizontally(initialOffsetX = { -it }) + fadeIn(),
        exit = slideOutHorizontally(targetOffsetX = { -it }) + fadeOut(),
        modifier = Modifier.fillMaxHeight().width(320.dp).zIndex(20f)
    ) {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = bgColor.copy(alpha = 0.95f),
            border = BorderStroke(1.dp, borderColor)
        ) {
            Column(modifier = Modifier.fillMaxSize()) {
                // Content
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .verticalScroll(rememberScrollState())
                        .padding(vertical = 16.dp)
                ) {
                    SectionLabel(stringResource(R.string.section_controller), contentColor)
                    ControllerTypeSwitch(
                        current = controllerType,
                        isLight = themeMode == "Light",
                        onSelect = onControllerTypeChange
                    )

                    Spacer(modifier = Modifier.height(16.dp))
                    Box(modifier = Modifier.padding(horizontal = 24.dp).fillMaxWidth().height(1.dp).background(borderColor))
                    Spacer(modifier = Modifier.height(16.dp))

                    SectionLabel(stringResource(R.string.section_modes), contentColor)
                    
                    ModeItem(
                        title = stringResource(R.string.mode_gamepad),
                        subtitle = stringResource(R.string.mode_gamepad_hint, controllerType.label),
                        icon = Icons.Rounded.SportsEsports,
                        isActive = currentMode == 0,
                        onClick = { onModeSelect(0) },
                        titleColor, contentColor
                    )
                    
                    ModeItem(
                        title = stringResource(R.string.mode_trackpad),
                        subtitle = stringResource(R.string.mode_trackpad_hint),
                        icon = Icons.Rounded.Mouse,
                        isActive = currentMode == 1,
                        onClick = { onModeSelect(1) },
                        titleColor, contentColor
                    )
                    
                    ModeItem(
                        title = stringResource(R.string.mode_racing),
                        subtitle = stringResource(R.string.mode_racing_hint),
                        icon = Icons.Rounded.SportsMotorsports,
                        isActive = currentMode == 2,
                        onClick = { onModeSelect(2) },
                        titleColor, contentColor
                    )
                    
                    ModeItem(
                        title = stringResource(R.string.mode_layouts),
                        subtitle = stringResource(R.string.mode_layouts_hint),
                        icon = Icons.Rounded.DashboardCustomize,
                        isActive = false, 
                        onClick = { 
                            onLayoutsClick()
                            onDismiss()
                        },
                        titleColor, contentColor
                    )
                    
                    Spacer(modifier = Modifier.height(16.dp))
                    Box(modifier = Modifier.padding(horizontal = 24.dp).fillMaxWidth().height(1.dp).background(borderColor))
                    Spacer(modifier = Modifier.height(16.dp))

                    SectionLabel(stringResource(R.string.section_system), contentColor)
                    
                    // Only when there is something to say. A new version is the
                    // one piece of news worth putting in front of somebody who
                    // came here to play, and it goes no further than a line in
                    // this menu — no dialog, nothing that interrupts a game.
                    // The menu stays open while it downloads: the progress is
                    // written here, and closing it would leave the tap looking
                    // like it did nothing.
                    if (updateLabel != null) {
                        SystemItem(updateLabel, Icons.Rounded.Update, accentPrimary, onUpdateClick)
                    }

                    SystemItem(stringResource(R.string.action_settings), Icons.Rounded.Settings, contentColor) {
                        onSettingsClick()
                        onDismiss()
                    }
                    SystemItem(stringResource(R.string.action_about), Icons.Rounded.Info, contentColor) {
                         onAboutClick()
                         onDismiss()
                    }
                    SystemItem(stringResource(R.string.action_help), Icons.Rounded.Help, contentColor) {
                        onHelpClick()
                        onDismiss()
                    }
                }
                
                // Footer: the one action that depends on where you actually are.
                // It used to be a red "Disconnect PC" at all times, offering to
                // end a session that in most cases had never started.
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(if(themeMode == "Light") Color(0xFFF5F5F5) else Color(0xFF0F1218))
                        .padding(16.dp)
                ) {
                    if (isConnected) {
                        Button(
                            onClick = onDisconnect,
                            modifier = Modifier.fillMaxWidth().height(48.dp).border(1.dp, Color.Red.copy(alpha = 0.2f), RoundedCornerShape(8.dp)),
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFD32F2F)),
                            shape = RoundedCornerShape(8.dp)
                        ) {
                            Icon(Icons.Rounded.PowerSettingsNew, "Disconnect", modifier = Modifier.size(16.dp), tint = Color.White)
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(stringResource(R.string.action_disconnect_pc), fontSize = 12.sp, fontWeight = FontWeight.Bold, color = Color.White)
                        }
                    } else {
                        // Quiet, because connecting is the ordinary thing to do here
                        // and red is reserved for the action that undoes something.
                        OutlinedButton(
                            onClick = onConnect,
                            modifier = Modifier.fillMaxWidth().height(48.dp),
                            shape = RoundedCornerShape(8.dp),
                            border = BorderStroke(1.dp, accentPrimary.copy(alpha = 0.5f))
                        ) {
                            Text(stringResource(R.string.action_connect_pc), fontSize = 12.sp, fontWeight = FontWeight.Bold, color = accentPrimary)
                        }
                    }
                }
            }
        }
    }
}

/** Compact three-way controller switch — the "quick change" the sidebar is for. */
@Composable
private fun ControllerTypeSwitch(
    current: ControllerType,
    isLight: Boolean,
    onSelect: (ControllerType) -> Unit
) {
    val accent = Color(0xFF0d59f2)
    Row(
        modifier = Modifier
            .padding(horizontal = 24.dp)
            .fillMaxWidth()
            .height(44.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(if (isLight) Color.Black.copy(alpha = 0.05f) else Color.White.copy(alpha = 0.05f))
            .padding(4.dp)
    ) {
        ControllerType.choices.forEach { type ->
            val selected = type == current
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxHeight()
                    .clip(RoundedCornerShape(10.dp))
                    .background(if (selected) accent else Color.Transparent)
                    .clickable { onSelect(type) },
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = when (type) {
                        ControllerType.XBOX360 -> "XBOX"
                        ControllerType.DUALSHOCK4 -> "DS4"
                        ControllerType.DUALSHOCK3 -> "DS3"
                        ControllerType.BUZZ -> "BUZZ"
                    },
                    color = if (selected) Color.White else if (isLight) Color(0xFF555555) else Color.White.copy(alpha = 0.5f),
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Black,
                    letterSpacing = 1.sp
                )
            }
        }
    }
}

@Composable
private fun SectionLabel(label: String, color: Color) {
    Text(
        text = label,
        modifier = Modifier.padding(horizontal = 24.dp, vertical = 8.dp),
        color = color,
        fontSize = 11.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = 2.sp
    )
}

@Composable
private fun ModeItem(
    title: String,
    subtitle: String,
    icon: ImageVector,
    isActive: Boolean,
    onClick: () -> Unit,
    titleColor: Color,
    subtitleColor: Color
) {
    val accentColor = Color(0xFF0d59f2)
    
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onClick() }
            .background(
                if (isActive) Brush.horizontalGradient(listOf(accentColor.copy(alpha = 0.15f), Color.Transparent))
                else Brush.horizontalGradient(listOf(Color.Transparent, Color.Transparent))
            )
            .drawBehind {
                if (isActive) {
                    drawLine(accentColor, Offset(0f, 0f), Offset(0f, size.height), strokeWidth = 8f)
                }
            }
            .padding(horizontal = 24.dp, vertical = 12.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            Icon(
                icon, 
                contentDescription = null, 
                tint = if (isActive) accentColor else subtitleColor,
                modifier = Modifier.size(24.dp).then(
                    if (isActive) Modifier.shadow(8.dp, CircleShape, spotColor = accentColor) else Modifier
                )
            )
            
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    title, 
                    color = if (isActive) titleColor else subtitleColor, 
                    fontSize = 14.sp, 
                    fontWeight = if (isActive) FontWeight.Bold else FontWeight.Medium
                )
                Text(subtitle, color = if (isActive) accentColor.copy(alpha = 0.7f) else subtitleColor.copy(alpha = 0.7f), fontSize = 10.sp)
            }
            
            if (isActive) {
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .background(accentColor, CircleShape)
                        .shadow(8.dp, CircleShape, spotColor = accentColor)
                )
            }
        }
    }
}

@Composable
private fun SystemItem(label: String, icon: ImageVector, color: Color, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onClick() }
            .padding(horizontal = 24.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Icon(icon, null, tint = color, modifier = Modifier.size(20.dp))
        Text(label, color = color, fontSize = 14.sp, fontWeight = FontWeight.Medium)
    }
}
