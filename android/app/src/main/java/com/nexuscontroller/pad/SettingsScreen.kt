package com.nexuscontroller.pad

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown


import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import kotlinx.coroutines.delay

data class SettingsState(
    val themeMode: String = "Dark", // Dark, Neon, Light
    val keepScreenOn: Boolean = true,
    val hapticEnabled: Boolean = true, // Master Rumble toggle
    val hapticStrength: Float = 0.85f,
    val gyroEnabled: Boolean = false,
    val gyroSensitivity: Float = 0.4f,
    val touchVibration: Boolean = true, // Master Touch vibration toggle
    val autoReconnect: Boolean = true,
    val deviceName: String = "Player 1",
    val touchSensitivity: Float = 1.0f,
    val controllerType: ControllerType = ControllerType.XBOX360,
    /** Guide must be held rather than tapped. On by default — see PSCenterButton. */
    val guideHold: Boolean = true
)

/**
 * Tab identities. Deliberately not the visible labels: those are translated, and
 * a `when` branching on a translated string picks the wrong arm the moment the
 * phone is not in English.
 */
private const val TAB_CONTROLS = "controls"
private const val TAB_VISUALS = "visuals"
private const val TAB_ACCOUNT = "account"

@Composable
fun SettingsScreen(
    isVisible: Boolean,
    onBack: () -> Unit,
    state: SettingsState,
    onThemeChange: (String) -> Unit,
    onScreenOnToggle: (Boolean) -> Unit,
    onHapticToggle: (Boolean) -> Unit,
    onHapticStrengthChange: (Float) -> Unit,
    onGyroToggle: (Boolean) -> Unit,
    onGyroSensitivityChange: (Float) -> Unit,
    onCalibrateGyro: () -> Unit, // New Param
    onTouchVibrationToggle: (Boolean) -> Unit,
    onGuideHoldToggle: (Boolean) -> Unit,
    onAutoReconnectToggle: (Boolean) -> Unit,
    onDeviceNameChange: (String) -> Unit,
    onTouchSensitivityChange: (Float) -> Unit,
    onControllerTypeChange: (ControllerType) -> Unit,
    onSave: () -> Unit,
    onReset: () -> Unit
) {
    if (!isVisible) return

    // Theme Color Logic (Local to this screen for now, or redundant with MainActivity)
    val bgColor = when(state.themeMode) {
        "Light" -> AppColors.BackgroundLight
        "Neon" -> Color.Black
        else -> AppColors.BackgroundDark
    }
    
    val contentBgAlpha = if(state.themeMode == "Light") 0.05f else 0.4f
    val sidebarBgAlpha = if(state.themeMode == "Light") 0.95f else 0.8f

    Box(
        modifier = Modifier
            .fillMaxSize()
            .zIndex(100f) // Ensure it's on top
            .background(bgColor)
    ) {
        // Carbon Pattern Background (Only visible in Dark)
        if (state.themeMode == "Dark") {
            CarbonBackgroundPattern()
        }

        // Identity, not text: the tab drives a `when`, so translating the key would
        // silently send a Polish phone to the fallback branch.
        var currentTab by remember { mutableStateOf(TAB_CONTROLS) }

        val isLightMode = state.themeMode == "Light"
        val textColor = if(isLightMode) Color(0xFF0D47A1) else Color.White
        val componentBg = if(isLightMode) AppColors.BackgroundLight else AppColors.Surface
        val borderColor = if(isLightMode) Color(0xFF90CAF9) else Color.White.copy(alpha = 0.1f)

        Row(modifier = Modifier.fillMaxSize()) {
            // Sidebar Navigation
            SettingsSidebar(
                onBack = onBack,
                bgColor = bgColor,
                bgAlpha = sidebarBgAlpha,
                isLightMode = isLightMode,
                currentTab = currentTab,
                onTabSelected = { currentTab = it },
                onReset = onReset
            )

            // Content Area
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxHeight()
                    .background(bgColor.copy(alpha = contentBgAlpha))
            ) {
                SettingsContent(
                    state = state,
                    currentTab = currentTab,
                    onThemeChange = onThemeChange,
                    onScreenOnToggle = onScreenOnToggle,
                    onHapticToggle = onHapticToggle,
                    onHapticStrengthChange = onHapticStrengthChange,
                    onGyroToggle = onGyroToggle,
                    onGyroSensitivityChange = onGyroSensitivityChange,
                    onCalibrateGyro = onCalibrateGyro,
                    onTouchVibrationToggle = onTouchVibrationToggle,
                    onGuideHoldToggle = onGuideHoldToggle,
                    onAutoReconnectToggle = onAutoReconnectToggle,
                    onDeviceNameChange = onDeviceNameChange,
                    onTouchSensitivityChange = onTouchSensitivityChange,
                    onControllerTypeChange = onControllerTypeChange,
                    onBack = onBack,
                    isLightMode = state.themeMode == "Light"
                )
            }
        }
    }
}

@Composable
fun CarbonBackgroundPattern() {
    Canvas(modifier = Modifier.fillMaxSize()) {
        val patternColor = Color(0xFF1c263a)
        val backgroundColor = Color(0xFF101622)
        val dotRadius = 0.5.dp.toPx()
        val spacing = 4.dp.toPx()

        drawRect(color = backgroundColor)

        // Draw radial dots pattern manually
        for (x in 0..size.width.toInt() step spacing.toInt()) {
            for (y in 0..size.height.toInt() step spacing.toInt()) {
                val offsetX = if ((y / spacing.toInt()) % 2 == 0) 0f else 2.dp.toPx()
                drawCircle(
                    color = patternColor,
                    radius = dotRadius,
                    center = Offset(x.toFloat() + offsetX, y.toFloat())
                )
            }
        }
    }
}

@Composable
fun SettingsSidebar(
    onBack: () -> Unit,
    bgColor: Color,
    bgAlpha: Float,
    isLightMode: Boolean,
    currentTab: String,
    onTabSelected: (String) -> Unit,
    onReset: () -> Unit
) {
    val textColor = if(isLightMode) Color(0xFF0D47A1) else Color.White
    val borderColor = if(isLightMode) Color(0xFF90CAF9) else Color.White.copy(alpha = 0.1f)

    Column(
        modifier = Modifier
            .width(280.dp)
            .fillMaxHeight()
            .background(bgColor.copy(alpha = bgAlpha))
            .border(
                width = 1.dp,
                color = borderColor
            )
            .padding(top = 8.dp)
    ) {
        // Header
        Row(
            modifier = Modifier.padding(horizontal = 24.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
                Box(
                    modifier = Modifier
                        .size(40.dp)
                        .shadow(4.dp, RoundedCornerShape(8.dp))
                        .clip(RoundedCornerShape(8.dp))
                        .background(if(isLightMode) Color.White else AppColors.Surface) // Opaque (White for light is redundant but safe)
                        .clickable { onBack() },
                    contentAlignment = Alignment.Center
                ) {
                Icon(
                    Icons.Rounded.ArrowBack,
                    contentDescription = stringResource(R.string.action_back),
                    tint = textColor
                )
            }
            Text(
                stringResource(R.string.settings_title),
                color = textColor,
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold
            )
        }

        Spacer(modifier = Modifier.height(24.dp))

        // Navigation Items
        Column(modifier = Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val tabs = listOf(
                Triple(TAB_CONTROLS, stringResource(R.string.settings_controls), Icons.Rounded.SportsEsports),
                Triple(TAB_VISUALS, stringResource(R.string.settings_visuals), Icons.Rounded.Visibility),
                Triple(TAB_ACCOUNT, stringResource(R.string.settings_account), Icons.Rounded.AccountCircle)
            )

            tabs.forEach { (key, label, icon) ->
                SettingsNavItem(
                    label = label,
                    icon = icon,
                    isActive = currentTab == key,
                    textColor = textColor,
                    onClick = { onTabSelected(key) }
                )
            }
        }

        Spacer(modifier = Modifier.weight(1f))

        // Reset Button at Bottom
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(12.dp))
                    .border(1.dp, Color.Red.copy(alpha = 0.3f), RoundedCornerShape(12.dp))
                    .clickable { onReset() }
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                Icon(
                    Icons.Rounded.RestartAlt,
                    contentDescription = null,
                    tint = Color.Red.copy(alpha = 0.7f)
                )
                Text(
                    stringResource(R.string.settings_reset),
                    color = Color.Red.copy(alpha = 0.7f),
                    fontWeight = FontWeight.Bold,
                    fontSize = 14.sp
                )
            }
        }
    }
}

@Composable
fun SettingsNavItem(label: String, icon: ImageVector, isActive: Boolean, textColor: Color, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(
                if (isActive) AppColors.Primary else Color.Transparent
            )
            .clickable { onClick() }
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Icon(
            icon,
            contentDescription = null,
            tint = if (isActive) Color.White else textColor.copy(alpha = 0.6f)
        )
        Text(
            label,
            color = if (isActive) Color.White else textColor.copy(alpha = 0.6f),
            fontWeight = FontWeight.Medium
        )
        if (isActive) {
            Spacer(modifier = Modifier.weight(1f))
            Box(
                modifier = Modifier
                    .size(6.dp)
                    .background(Color.White.copy(alpha = 0.5f), CircleShape)
            )
        }
    }
}

@Composable
fun SettingsContent(
    state: SettingsState,
    currentTab: String,
    onThemeChange: (String) -> Unit,
    onScreenOnToggle: (Boolean) -> Unit,
    onHapticToggle: (Boolean) -> Unit,
    onHapticStrengthChange: (Float) -> Unit,
    onGyroToggle: (Boolean) -> Unit,
    onGyroSensitivityChange: (Float) -> Unit,
    onCalibrateGyro: () -> Unit, // New Param
    onTouchVibrationToggle: (Boolean) -> Unit,
    onGuideHoldToggle: (Boolean) -> Unit,
    onAutoReconnectToggle: (Boolean) -> Unit,
    onDeviceNameChange: (String) -> Unit,
    onTouchSensitivityChange: (Float) -> Unit,
    onControllerTypeChange: (ControllerType) -> Unit,
    onBack: () -> Unit,
    isLightMode: Boolean
) {
    val contentTextColor = if(isLightMode) Color(0xFF0D47A1) else Color.White
    val containerColor = if(isLightMode) Color(0xFFBBDEFB) else Color.White.copy(alpha = 0.05f) 
    val borderColor = if(isLightMode) Color(0xFF90CAF9) else Color.White.copy(alpha = 0.1f) 

    Column(
        modifier = Modifier
            .padding(32.dp)
            .fillMaxWidth()
            .verticalScroll(rememberScrollState()) 
    ) {
        when (currentTab) {
            TAB_VISUALS -> {
                // VISUALS SECTION
                SettingsSectionHeader(stringResource(R.string.settings_theme), contentTextColor)
                // Theme Toggle
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(80.dp)
                        .padding(bottom = 24.dp),
                    horizontalArrangement = Arrangement.Center
                ) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .fillMaxHeight()
                            .background(containerColor, RoundedCornerShape(16.dp))
                            .border(1.dp, borderColor, RoundedCornerShape(16.dp))
                            .padding(6.dp)
                    ) {
                        Row(modifier = Modifier.fillMaxSize()) {
                            // Label translated, key never: the theme name is also
                            // what gets stored and compared against.
                            ThemeRadioButton(stringResource(R.string.theme_dark), selected = state.themeMode == "Dark", modifier = Modifier.weight(1f), isLightMode) { onThemeChange("Dark") }
                            ThemeRadioButton(stringResource(R.string.theme_neon), selected = state.themeMode == "Neon", modifier = Modifier.weight(1f), isLightMode) { onThemeChange("Neon") }
                            ThemeRadioButton(stringResource(R.string.theme_light), selected = state.themeMode == "Light", modifier = Modifier.weight(1f), isLightMode) { onThemeChange("Light") }
                        }
                    }
                }
                
                SettingsSectionHeader(stringResource(R.string.settings_display), contentTextColor)
                SettingsToggleItem(
                    title = stringResource(R.string.settings_keep_awake),
                    subtitle = stringResource(R.string.settings_keep_awake_hint),
                    icon = Icons.Rounded.Smartphone,
                    checked = state.keepScreenOn,
                    onCheckedChange = onScreenOnToggle,
                    textColor = contentTextColor,
                    containerColor = containerColor,
                    borderColor = borderColor
                )
            }
            TAB_CONTROLS -> {
                // CONTROLS SECTION
                SettingsSectionHeader(stringResource(R.string.settings_controller_type), contentTextColor)
                Text(
                    stringResource(R.string.settings_controller_type_hint),
                    color = contentTextColor.copy(alpha = 0.5f),
                    fontSize = 12.sp
                )
                Spacer(modifier = Modifier.height(12.dp))
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(72.dp),
                    horizontalArrangement = Arrangement.Center
                ) {
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .background(containerColor, RoundedCornerShape(16.dp))
                            .border(1.dp, borderColor, RoundedCornerShape(16.dp))
                            .padding(6.dp)
                    ) {
                        Row(modifier = Modifier.fillMaxSize()) {
                            ControllerType.entries.forEach { type ->
                                ThemeRadioButton(
                                    text = type.label,
                                    selected = state.controllerType == type,
                                    modifier = Modifier.weight(1f),
                                    isLightMode = isLightMode
                                ) { onControllerTypeChange(type) }
                            }
                        }
                    }
                }

                Spacer(modifier = Modifier.height(24.dp))

                SettingsToggleItem(
                    title = stringResource(R.string.settings_guide_hold),
                    subtitle = stringResource(R.string.settings_guide_hold_hint),
                    icon = Icons.Rounded.TouchApp,
                    checked = state.guideHold,
                    onCheckedChange = onGuideHoldToggle,
                    textColor = contentTextColor,
                    containerColor = containerColor,
                    borderColor = borderColor
                )

                Spacer(modifier = Modifier.height(24.dp))

                SettingsSectionHeader(stringResource(R.string.settings_haptics), contentTextColor)
                SettingsToggleItem(
                    title = stringResource(R.string.settings_haptic_feedback),
                    subtitle = stringResource(R.string.settings_haptic_feedback_hint),
                    icon = Icons.Rounded.Vibration,
                    checked = state.touchVibration, 
                    onCheckedChange = onTouchVibrationToggle,
                    textColor = contentTextColor,
                    containerColor = containerColor,
                    borderColor = borderColor
                )

                Spacer(modifier = Modifier.height(16.dp))

                SettingsToggleItem(
                    title = stringResource(R.string.settings_rumble),
                    subtitle = stringResource(R.string.settings_rumble_hint),
                    icon = Icons.Rounded.Vibration,
                    checked = state.hapticEnabled,
                    onCheckedChange = onHapticToggle,
                    textColor = contentTextColor,
                    containerColor = containerColor,
                    borderColor = borderColor
                )

                // Show Slider if ANY haptics are enabled
                if (state.touchVibration || state.hapticEnabled) {
                     Spacer(modifier = Modifier.height(16.dp))
                     Text(stringResource(R.string.settings_haptic_intensity, (state.hapticStrength * 100).toInt()), color = contentTextColor, fontSize = 14.sp)
                     Spacer(modifier = Modifier.height(8.dp))
                     CustomSlider(value = state.hapticStrength, onValueChange = onHapticStrengthChange, enabled = true, trackColor = if(isLightMode) Color.Black.copy(alpha=0.1f) else Color.White.copy(alpha=0.1f))
                }
                
                Spacer(modifier = Modifier.height(24.dp))
                
                SettingsSectionHeader(stringResource(R.string.settings_trackpad), contentTextColor)
                Text(stringResource(R.string.settings_touch_sensitivity, String.format("%.1fx", state.touchSensitivity)), color = contentTextColor, fontSize = 14.sp)
                Spacer(modifier = Modifier.height(8.dp))
                CustomSlider(
                    value = (state.touchSensitivity - 0.1f) / 1.9f, 
                    onValueChange = { onTouchSensitivityChange(0.1f + it * 1.9f) }, 
                    enabled = true, 
                    trackColor = if(isLightMode) Color.Black.copy(alpha=0.1f) else Color.White.copy(alpha=0.1f)
                )

                Spacer(modifier = Modifier.height(24.dp))
                
                SettingsSectionHeader(stringResource(R.string.settings_connectivity), contentTextColor)
                SettingsToggleItem(
                    title = stringResource(R.string.settings_auto_reconnect),
                    subtitle = stringResource(R.string.settings_auto_reconnect_hint),
                    icon = Icons.Rounded.Link,
                    checked = state.autoReconnect,
                    onCheckedChange = onAutoReconnectToggle,
                    textColor = contentTextColor,
                    containerColor = containerColor,
                    borderColor = borderColor
                )

                Spacer(modifier = Modifier.height(24.dp))
                
                SettingsSectionHeader(stringResource(R.string.settings_sensors), contentTextColor)
                SettingsToggleItem(
                    title = stringResource(R.string.settings_gyro),
                    subtitle = stringResource(R.string.settings_gyro_hint),
                    icon = Icons.Rounded.Explore,
                    checked = state.gyroEnabled,
                    onCheckedChange = onGyroToggle,
                    textColor = contentTextColor,
                    containerColor = containerColor,
                    borderColor = borderColor
                )
                
                if (state.gyroEnabled) {
                     Spacer(modifier = Modifier.height(16.dp))
                     Text(stringResource(R.string.settings_gyro_sensitivity, (state.gyroSensitivity * 100).toInt()), color = contentTextColor, fontSize = 14.sp)
                     Spacer(modifier = Modifier.height(8.dp))
                     CustomSlider(value = state.gyroSensitivity, onValueChange = onGyroSensitivityChange, enabled = true, trackColor = if(isLightMode) Color.Black.copy(alpha=0.1f) else Color.White.copy(alpha=0.1f))
                     
                     Spacer(modifier = Modifier.height(16.dp))
                     
                     // Calibrate Button
                     Box(contentAlignment = Alignment.Center) {
                         Button(
                             onClick = { 
                                 onCalibrateGyro() 
                             },
                             colors = ButtonDefaults.buttonColors(containerColor = AppColors.Primary),
                             modifier = Modifier.fillMaxWidth().height(48.dp),
                             shape = RoundedCornerShape(12.dp)
                         ) {
                             Icon(Icons.Rounded.CenterFocusWeak, null, modifier=Modifier.size(18.dp))
                             Spacer(Modifier.width(8.dp))
                             Text(stringResource(R.string.settings_calibrate), fontWeight = FontWeight.Bold)
                         }
                     }
                }
            }
            TAB_ACCOUNT -> {
                // ACCOUNT SECTION
                SettingsSectionHeader(stringResource(R.string.settings_device_profile), contentTextColor)
                
                // TextField for Device Name
                OutlinedTextField(
                    value = state.deviceName,
                    onValueChange = { onDeviceNameChange(it) },
                    label = { Text(stringResource(R.string.settings_device_name)) },
                    modifier = Modifier.fillMaxWidth(),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = contentTextColor,
                        unfocusedTextColor = contentTextColor,
                        focusedBorderColor = AppColors.Primary,
                        unfocusedBorderColor = borderColor,
                        focusedLabelColor = AppColors.Primary,
                        unfocusedLabelColor = contentTextColor.copy(alpha = 0.6f)
                    ),
                    singleLine = true,
                    trailingIcon = {
                        IconButton(onClick = { /* MainActivity handles save on every change, but this provides visual confirmation */ }) {
                            Icon(
                                Icons.Rounded.CheckCircle,
                                contentDescription = stringResource(R.string.action_save),
                                tint = AppColors.Primary
                            )
                        }
                    }
                )
                
                Spacer(modifier = Modifier.height(24.dp))
                
            }
        }
    }
}




@Composable
fun SettingsSectionHeader(title: String, textColor: Color) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        modifier = Modifier.padding(bottom = 24.dp)
    ) {
        Text(title, color = textColor, fontSize = 18.sp, fontWeight = FontWeight.Bold)
        Box(
            modifier = Modifier
                .weight(1f)
                .height(1.dp)
                .background(textColor.copy(alpha = 0.1f))
        )
    }
}

@Composable
fun ThemeRadioButton(text: String, selected: Boolean, modifier: Modifier = Modifier, isLightMode: Boolean, onClick: () -> Unit) {
    val textColor = if(selected) Color.White else if(isLightMode) Color(0xFF555555) else Color.White.copy(alpha = 0.5f)
    Box(
        modifier = modifier
            .fillMaxHeight()
            .clip(RoundedCornerShape(12.dp)) // Match parent better
            .background(if (selected) AppColors.Primary else Color.Transparent)
            .clickable { onClick() },
        contentAlignment = Alignment.Center
    ) {
        Text(
            text,
            color = textColor,
            fontWeight = FontWeight.Bold,
            fontSize = 16.sp // Slightly larger text
        )
    }
}

@Composable
fun SettingsToggleItem(
    title: String,
    subtitle: String,
    icon: ImageVector,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    textColor: Color,
    containerColor: Color,
    borderColor: Color
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(containerColor, RoundedCornerShape(16.dp))
            .border(1.dp, borderColor, RoundedCornerShape(16.dp))
            .clip(RoundedCornerShape(16.dp))
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null
            ) { onCheckedChange(!checked) }
            .padding(horizontal = 24.dp, vertical = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(48.dp)
                    .background(AppColors.Primary.copy(alpha = 0.1f), RoundedCornerShape(12.dp))
                    .border(1.dp, AppColors.Primary.copy(alpha = 0.2f), RoundedCornerShape(12.dp)),
                contentAlignment = Alignment.Center
            ) {
                Icon(icon, null, tint = AppColors.Primary)
            }
            Column {
                Text(title, color = textColor, fontWeight = FontWeight.Bold, fontSize = 16.sp)
                Text(subtitle, color = textColor.copy(alpha = 0.4f), fontSize = 12.sp)
            }
        }
        CustomSwitch(checked = checked, onCheckedChange = onCheckedChange)
    }
}

@Composable
fun CustomSwitch(checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    val trackColor = if (checked) AppColors.Primary else Color.White.copy(alpha = 0.1f)
    val thumbAlign = if (checked) Alignment.CenterEnd else Alignment.CenterStart
    
    Box(
        modifier = Modifier
            .width(50.dp)
            .height(30.dp)
            .clip(CircleShape)
            .background(trackColor)
            .clickable { onCheckedChange(!checked) }
            .padding(2.dp),
        contentAlignment = thumbAlign
    ) {
        Box(
            modifier = Modifier
                .size(26.dp)
                .background(Color.White, CircleShape)
        )
    }
}

@Composable
fun CustomSlider(value: Float, onValueChange: (Float) -> Unit, enabled: Boolean = true, trackColor: Color = Color.White.copy(alpha = 0.1f)) {
    val primary = AppColors.Primary
    
    // Use Standard Material3 Slider for reliability
    Slider(
        value = value,
        onValueChange = onValueChange,
        enabled = enabled,
        valueRange = 0f..1f,
        colors = SliderDefaults.colors(
            thumbColor = Color.White,
            activeTrackColor = primary,
            inactiveTrackColor = trackColor
        ),
        modifier = Modifier.fillMaxWidth()
    )
}
