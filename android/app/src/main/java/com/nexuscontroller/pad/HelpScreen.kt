package com.nexuscontroller.pad

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ArrowBack
import androidx.compose.material.icons.rounded.ExpandMore
import androidx.compose.material.icons.rounded.Gamepad
import androidx.compose.material.icons.rounded.Speed
import androidx.compose.material.icons.rounded.SupportAgent
import androidx.compose.material.icons.rounded.WifiOff
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// Theme-aware colors
data class HelpThemeColors(
    val background: Color,
    val surface: Color,
    val surfaceBorder: Color,
    val textPrimary: Color,
    val textSecondary: Color,
    val primary: Color,
    val iconRed: Color,
    val bgRed: Color,
    val iconAmber: Color,
    val bgAmber: Color,
    val iconBlue: Color,
    val bgBlue: Color
)

@Composable
fun getHelpThemeColors(themeMode: String): HelpThemeColors {
    val isLight = themeMode == "Light"
    
    return if (isLight) {
        HelpThemeColors(
            background = Color(0xFFF3F4F6),
            surface = Color.White,
            surfaceBorder = Color(0xFFE5E7EB),
            textPrimary = Color(0xFF111827),
            textSecondary = Color(0xFF6B7280),
            primary = Color(0xFF0D59F2),
            iconRed = Color(0xFFDC2626),
            bgRed = Color(0xFFFEE2E2),
            iconAmber = Color(0xFFD97706),
            bgAmber = Color(0xFFFEF3C7),
            iconBlue = Color(0xFF2563EB),
            bgBlue = Color(0xFFDBEAFE)
        )
    } else {
        HelpThemeColors(
            background = Color(0xFF101622),
            surface = Color(0xFF1B1F27),
            surfaceBorder = Color(0xFF3B4354),
            textPrimary = Color.White,
            textSecondary = Color(0xFF9CA3AF),
            primary = Color(0xFF0D59F2),
            iconRed = Color(0xFFEF4444),
            bgRed = Color(0x1AEF4444),
            iconAmber = Color(0xFFF59E0B),
            bgAmber = Color(0x1AF59E0B),
            iconBlue = Color(0xFF3B82F6),
            bgBlue = Color(0x1A3B82F6)
        )
    }
}

data class FAQStep(val text: String, val highlight: String? = null)
data class FAQItemData(
    val title: String,
    val subtitle: String,
    val icon: ImageVector,
    val iconColor: Color, // Fallback or type identifier, actual color handled in composable ideally, but kept strict for now
    val iconBg: Color,
    val steps: List<FAQStep>
)

@Composable
fun HelpScreen(onBack: () -> Unit, themeMode: String) {
    val colors = getHelpThemeColors(themeMode)
    
    // We construct items properly inside or passed in, but strictly the icon colors need to match theme.
    // Ideally we should pass simple enums or types, but for now we'll dynamically set them here
    // Content comes from resources, and says what this app actually does: the
    // originals advised opening "Port 8080" and lowering a "Streaming Quality"
    // setting, neither of which exists here.
    val faqItems = listOf(
        FAQItemData(
            title = stringResource(R.string.help_notfound_title),
            subtitle = stringResource(R.string.help_notfound_subtitle),
            icon = Icons.Rounded.WifiOff,
            iconColor = colors.iconRed,
            iconBg = colors.bgRed,
            steps = listOf(
                FAQStep(stringResource(R.string.help_notfound_1)),
                FAQStep(stringResource(R.string.help_notfound_2)),
                FAQStep(stringResource(R.string.help_notfound_3)),
                FAQStep(stringResource(R.string.help_notfound_4)),
                FAQStep(stringResource(R.string.help_notfound_5)),
                FAQStep(stringResource(R.string.help_notfound_6))
            )
        ),
        FAQItemData(
            title = stringResource(R.string.help_lag_title),
            subtitle = stringResource(R.string.help_lag_subtitle),
            icon = Icons.Rounded.Speed,
            iconColor = colors.iconAmber,
            iconBg = colors.bgAmber,
            steps = listOf(
                FAQStep(stringResource(R.string.help_lag_1)),
                FAQStep(stringResource(R.string.help_lag_2)),
                FAQStep(stringResource(R.string.help_lag_3))
            )
        ),
        FAQItemData(
            title = stringResource(R.string.help_buttons_title),
            subtitle = stringResource(R.string.help_buttons_subtitle),
            icon = Icons.Rounded.Gamepad,
            iconColor = colors.iconBlue,
            iconBg = colors.bgBlue,
            steps = listOf(
                FAQStep(stringResource(R.string.help_buttons_1)),
                FAQStep(stringResource(R.string.help_buttons_2)),
                FAQStep(stringResource(R.string.help_buttons_3)),
                FAQStep(stringResource(R.string.help_buttons_4))
            )
        )
    )

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.background)
    ) {
        // Carbon Pattern Background (Only for Dark)
        if (themeMode == "Dark") {
            CarbonBackgroundPattern() 
        }
        
        Column(modifier = Modifier.fillMaxSize()) {
            // Header
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(colors.surface.copy(alpha = 0.9f))
                    .border(width = 0.dp, color = Color.Transparent) 
            ) {
                 // Bottom Border removed as requested ("remove upper strip")

                 Row(
                     modifier = Modifier.padding(horizontal = 24.dp, vertical = 12.dp),
                     verticalAlignment = Alignment.CenterVertically,
                     horizontalArrangement = Arrangement.SpaceBetween
                 ) {
                     IconButton(
                         onClick = onBack,
                         modifier = Modifier
                             .size(40.dp)
                             .background(Color.Transparent, CircleShape)
                     ) {
                         Icon(Icons.Rounded.ArrowBack, contentDescription = stringResource(R.string.action_back), tint = colors.textSecondary)
                     }
                     
                     Column(
                         modifier = Modifier.weight(1f),
                         horizontalAlignment = Alignment.CenterHorizontally
                     ) {
                         Text(
                             stringResource(R.string.help_title),
                             color = colors.textPrimary,
                             fontSize = 18.sp,
                             fontWeight = FontWeight.Bold,
                             letterSpacing = (-0.5).sp
                         )
                         Text(
                             stringResource(R.string.help_subtitle),
                             color = colors.textSecondary,
                             fontSize = 12.sp
                         )
                     }
                     
                     Spacer(modifier = Modifier.size(40.dp)) // Balance the back button
                 }
            }

            // Content
            LazyColumn(
                modifier = Modifier.weight(1f),
                contentPadding = PaddingValues(24.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                itemsIndexed(faqItems) { index, item ->
                    AccordionItem(item, colors)
                }
                
                item {
                    Spacer(modifier = Modifier.height(96.dp)) // Bottom padding for footer
                }
            }
        }
        
        // Footer
        Box(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .background(colors.surface)
                .border(1.dp, colors.surfaceBorder.copy(alpha=0.5f)) // Border Top essentially
        ) {
            // Border Top
             Box(
                 modifier = Modifier
                     .align(Alignment.TopStart)
                     .fillMaxWidth()
                     .height(1.dp)
                     .background(colors.surfaceBorder)
             )
            
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                Column(modifier = Modifier.weight(1f).displayCutoutPadding()) {
                    Text(
                        stringResource(R.string.help_stuck_title),
                        color = colors.textPrimary,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold
                    )
                    // Points at the log on the PC. The button here used to say
                    // "Contact Support" and its onClick was a TODO — a promise of
                    // a support team that does not exist.
                    Text(
                        stringResource(R.string.help_stuck_body),
                        color = colors.textSecondary,
                        fontSize = 12.sp
                    )
                }
            }
        }
    }
}

@Composable
fun AccordionItem(item: FAQItemData, colors: HelpThemeColors) {
    var expanded by remember { mutableStateOf(false) }
    val rotation by animateFloatAsState(targetValue = if (expanded) 180f else 0f, label = "rotation")
    
    val borderColor by animateColorAsState(
        targetValue = if (expanded) colors.primary.copy(alpha = 0.5f) else colors.surfaceBorder,
        label = "border"
    )
    
    val titleColor by animateColorAsState(
        targetValue = if (expanded && colors.surface != Color.White) item.iconColor.copy(alpha = 0.8f) else colors.textPrimary,
        label = "title"
    )

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(colors.surface)
            .border(1.dp, borderColor, RoundedCornerShape(12.dp))
            .clickable(
                indication = null,
                interactionSource = remember { MutableInteractionSource() }
            ) { expanded = !expanded }
    ) {
        // Header
        Row(
            modifier = Modifier.padding(20.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Row(
                modifier = Modifier.weight(1f), // Take available space to push arrow
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    modifier = Modifier
                        .size(40.dp)
                        .background(item.iconBg, CircleShape)
                        .border(1.dp, item.iconColor.copy(alpha = 0.2f), CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(item.icon, null, tint = item.iconColor, modifier = Modifier.size(20.dp))
                }
                
                Column {
                    Text(
                        item.title,
                        color = titleColor,
                        fontWeight = FontWeight.Bold,
                        fontSize = 16.sp,
                        modifier = Modifier.fillMaxWidth() // Ensure text takes space if needed
                    )
                    AnimatedVisibility(!expanded) {
                        Text(
                            item.subtitle,
                            color = colors.textSecondary,
                            fontSize = 12.sp,
                            maxLines = 1
                        )
                    }
                }
            }
            
            Spacer(modifier = Modifier.width(16.dp)) // Min spacing
            
            Icon(
                Icons.Rounded.ExpandMore, 
                null, 
                tint = if(expanded) colors.primary else colors.textSecondary,
                modifier = Modifier.rotate(rotation)
            )
        }

        // Expanded Content
        AnimatedVisibility(
            visible = expanded,
            enter = expandVertically() + fadeIn(),
            exit = shrinkVertically() + fadeOut()
        ) {
            Column(modifier = Modifier.padding(start = 20.dp, end = 20.dp, bottom = 20.dp)) {
                // Divider
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(1.dp)
                        .background(colors.surfaceBorder.copy(alpha = 0.5f))
                )
                
                Spacer(modifier = Modifier.height(16.dp))
                
                Column(
                    modifier = Modifier.padding(start = 56.dp), // align with title
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    item.steps.forEachIndexed { index, step ->
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Box(
                                modifier = Modifier
                                    .size(20.dp)
                                    .background(colors.surfaceBorder, CircleShape),
                                contentAlignment = Alignment.Center
                            ) {
                                Text(
                                    (index + 1).toString(),
                                    color = colors.textPrimary, // Was White
                                    fontSize = 10.sp,
                                    fontWeight = FontWeight.Bold
                                )
                            }
                            
                            val text = buildAnnotatedString {
                                if (step.highlight != null && step.text.contains(step.highlight)) {
                                    val parts = step.text.split(step.highlight)
                                    append(parts[0])
                                    withStyle(SpanStyle(color = colors.textPrimary, fontWeight = FontWeight.Bold)) { // Was White
                                        append(step.highlight)
                                    }
                                    if (parts.size > 1) append(parts[1])
                                } else {
                                    append(step.text)
                                }
                            }
                            
                            Text(text, color = colors.textSecondary.copy(alpha=0.8f), fontSize = 14.sp, lineHeight = 20.sp)
                        }
                    }
                }
            }
        }
    }
}
