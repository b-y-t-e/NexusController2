package com.nexuscontroller.pad

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Save
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.snapshots.SnapshotStateMap
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex

@Composable
fun LayoutEditorScreen(
    initialConfigs: Map<String, CompConfig>,
    controllerType: ControllerType,
    onBack: () -> Unit,
    onSave: (Map<String, CompConfig>) -> Unit,
    themeMode: String
) {
    val isLight = themeMode == "Light"
    val bgColor = if(isLight) AppColors.BackgroundLight else AppColors.BackgroundDark
    val textColor = if(isLight) Color.Black else Color.White
    
    // Local copy of configs to edit
    val configs = remember { 
        mutableStateMapOf<String, CompConfig>().apply {
            initialConfigs.forEach { (k, v) -> put(k, v.copy()) }
        }
    }
    
    var selectedId by remember { mutableStateOf<String?>(null) }
    
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.radialGradient(
                    colors = if(isLight) listOf(Color.White, Color(0xFFE5E7EB)) 
                             else listOf(Color(0xFF1F2937), Color.Black),
                    radius = 2000f
                )
            )
    ) {
        // Grid background for the "Scratch" editor feel
        EditorGridBackground(isLight)

        // Workspace
        BoxWithConstraints(Modifier.fillMaxSize()) {
            val w = maxWidth
            val h = maxHeight
            
            // Render components
            configs.keys.toList().forEach { id ->
                val conf = configs[id]!!
                EditableComponent(
                    id = id,
                    isEditMode = true,
                    isSelected = selectedId == id,
                    config = conf,
                    onSelect = { selectedId = it },
                    onDelete = { configs.remove(id); selectedId = null }
                ) {
                    RenderComponentShape(id, conf, isLight, controllerType)
                }
            }
        }

        // Top Toolbar
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp)
                .zIndex(100f),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Cancel
            Surface(
                onClick = onBack,
                shape = CircleShape,
                color = if(isLight) Color.White else Color(0xFF2C2C2C),
                tonalElevation = 4.dp,
                modifier = Modifier.size(48.dp)
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(Icons.Default.Close, "Cancel", tint = textColor)
                }
            }

            Text(
                "Layout Editor", 
                color = textColor, 
                fontWeight = FontWeight.Bold, 
                fontSize = 18.sp
            )

            // Save
            Button(
                onClick = { onSave(configs.toMap()) },
                colors = ButtonDefaults.buttonColors(containerColor = AppColors.Primary),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.height(44.dp)
            ) {
                Icon(Icons.Default.Save, null, Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("SAVE", fontWeight = FontWeight.ExtraBold)
            }
        }

        // Bottom Sizing Strip (Slider)
        if (selectedId != null) {
            Box(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .padding(start = 32.dp, end = 32.dp)
                    .height(64.dp)
                    .clip(RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp))
                    .background(Color.Black.copy(alpha = 0.5f))
                    .border(1.dp, Color.White.copy(alpha = 0.1f), RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp))
                    .padding(horizontal = 24.dp),
                contentAlignment = Alignment.Center
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("SIZE", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.width(16.dp))
                    Slider(
                        value = configs[selectedId!!]?.scale ?: 1f,
                        onValueChange = { configs[selectedId!!]?.scale = it },
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
                        "${String.format("%.1f", configs[selectedId!!]?.scale ?: 1f)}x",
                        color = Color.White,
                        fontSize = 12.sp,
                        fontFamily = FontFamily.Monospace
                    )
                }
            }
        }
    }
}


/** Draws a component the way it will look in play mode, for the given controller type. */
@Composable
fun RenderComponentShape(
    id: String,
    config: CompConfig,
    isLight: Boolean,
    controllerType: ControllerType = ControllerType.XBOX360
) {
    val bg = if (isLight) Color(0xFFE5E7EB) else AppColors.Surface
    val txt = if (isLight) Color.Black else Color.White

    when {
        id == "L2" -> PSTriggerShape(Glyphs.trigger(controllerType, true), Modifier, bg, txt, isLeft = true) {}
        id == "R2" -> PSTriggerShape(Glyphs.trigger(controllerType, false), Modifier, bg, txt, isLeft = false) {}
        id == "L1" -> PSBumperShape(Glyphs.bumper(controllerType, true), Modifier, bg, txt, 0, {}, { _, _ -> })
        id == "R1" -> PSBumperShape(Glyphs.bumper(controllerType, false), Modifier, bg, txt, 0, {}, { _, _ -> })
        id == "DPAD" -> PSDpadDetailed(bg, txt) { _, _ -> }
        id == "FACE" -> PSFaceButtonsDetailed(bg, controllerType) { _, _ -> }
        id == "L_STICK" -> PSJoystickSimple("L", bg, AppColors.Accent) { _, _ -> }
        id == "R_STICK" -> PSJoystickSimple("R", bg, AppColors.Accent) { _, _ -> }
        id == "SHARE" || id == "OPTIONS" ->
            PSCenterButton(Glyphs.center(controllerType, id), Modifier, bg, 0, false, isGuide = false, onVibrate = {}) { _, _ -> }
        id == "PS" -> PSCenterButton("PS", Modifier, bg, 0, false, isGuide = true, onVibrate = {}) { _, _ -> }
        id == "BUZZ_RED" -> BuzzBuzzerButton({}) { _, _ -> }
        id.startsWith("BUZZ_") -> {
            val spec = BuzzAnswerSpec.forId(id)
            if (spec != null) BuzzAnswerButton(spec.label, spec.color, spec.mask, {}) { _, _ -> }
        }
        id.startsWith("BTN_") ->
            PSCenterButton("BTN", Modifier, bg, 0, false, isGuide = false, onVibrate = {}) { _, _ -> }
    }
}
