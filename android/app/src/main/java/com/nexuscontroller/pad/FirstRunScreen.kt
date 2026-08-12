package com.nexuscontroller.pad

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Shown once, on the very first run: which pad should this phone be?
 *
 * The app used to open on an Xbox 360 pad no matter what, and the controller type
 * lived three taps deep in a menu — so someone who installed it to be a Buzz
 * buzzer met the wrong device and had to go looking. Asking once, up front, costs
 * one screen and is never shown again.
 */
@Composable
fun FirstRunScreen(onChosen: (ControllerType) -> Unit) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(listOf(Color(0xFF1a202e), Color(0xFF0b0f16)))
            ),
        contentAlignment = Alignment.Center
    ) {
        // Scrollable, and laid out as short wide rows rather than tall tiles.
        // On a small old phone held in landscape there are barely 320 dp of
        // height: the tile version put half the choices under the bottom edge
        // with no way to reach them, which is a first-run screen that cannot be
        // completed.
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 14.dp)
        ) {
            Text(
                stringResource(R.string.first_run_title),
                color = Color.White,
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold
            )
            Text(
                stringResource(R.string.first_run_subtitle),
                color = Color.White.copy(alpha = 0.55f),
                fontSize = 12.sp
            )
            Spacer(Modifier.height(2.dp))

            ControllerType.choices.chunked(2).forEach { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    row.forEach { type -> PadChoice(type) { onChosen(type) } }
                }
            }
        }
    }
}

/** One choice: what the pad looks like, what it is, and what it is for. */
@Composable
private fun PadChoice(type: ControllerType, onClick: () -> Unit) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        modifier = Modifier
            .width(210.dp)
            .clip(RoundedCornerShape(14.dp))
            .background(Color.White.copy(alpha = 0.05f))
            .border(1.dp, Color.White.copy(alpha = 0.10f), RoundedCornerShape(14.dp))
            .clickable { onClick() }
            .padding(horizontal = 12.dp, vertical = 10.dp)
    ) {
        PadGlyph(type)
        Column {
            Text(
                type.label,
                color = Color.White,
                fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold
            )
            Text(
                stringResource(
                    when (type) {
                        ControllerType.XBOX360 -> R.string.pad_xbox_summary
                        ControllerType.DUALSHOCK4 -> R.string.pad_ds4_summary
                        ControllerType.DUALSHOCK3 -> R.string.pad_ds3_summary
                        ControllerType.BUZZ -> R.string.pad_buzz_summary
                    }
                ),
                color = Color.White.copy(alpha = 0.5f),
                fontSize = 10.sp
            )
        }
    }
}

/**
 * The smallest drawing that tells the three apart: a shape from each pad's own
 * face rather than an icon set nobody recognises.
 */
@Composable
private fun PadGlyph(type: ControllerType) {
    when (type) {
        ControllerType.BUZZ -> Box(
            Modifier
                .size(34.dp)
                .background(AppColors.BuzzRed, CircleShape)
        )
        ControllerType.XBOX360 -> Box(Modifier.size(34.dp), contentAlignment = Alignment.Center) {
            FaceDots(
                north = Color(0xFFF7C325), south = Color(0xFF37B35A),
                east = Color(0xFFE8443A), west = Color(0xFF3B78D8)
            )
        }
        ControllerType.DUALSHOCK4, ControllerType.DUALSHOCK3 -> Box(
            Modifier.size(34.dp), contentAlignment = Alignment.Center
        ) {
            val grey = Color.White.copy(alpha = 0.75f)
            FaceDots(north = grey, south = grey, east = grey, west = grey)
        }
    }
}

@Composable
private fun FaceDots(north: Color, south: Color, east: Color, west: Color) {
    Box(Modifier.size(34.dp)) {
        Dot(north, Modifier.align(Alignment.TopCenter))
        Dot(west, Modifier.align(Alignment.CenterStart))
        Dot(east, Modifier.align(Alignment.CenterEnd))
        Dot(south, Modifier.align(Alignment.BottomCenter))
    }
}

@Composable
private fun Dot(color: Color, modifier: Modifier) {
    Box(modifier.size(11.dp).background(color, CircleShape))
}
