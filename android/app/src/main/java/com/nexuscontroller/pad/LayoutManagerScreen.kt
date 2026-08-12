package com.nexuscontroller.pad

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex

// Colors from HTML/Tailwind config
private val PrimaryBlue = Color(0xFF389CFA)
private val BgDark = Color(0xFF0F1923)
private val SurfaceDark = Color(0xFF182735)
private val SurfaceDarker = Color(0xFF101A23)

data class LayoutItem(
    val title: String,
    val lastUsed: String,
    val isActive: Boolean = false,
    val gradientColors: List<Color>
)

@Composable
fun LayoutManagerScreen(
    layouts: List<String>,
    activeProfile: String,
    onBack: () -> Unit,
    onCreate: () -> Unit,
    onSelect: (String) -> Unit,
    onEdit: (String) -> Unit,
    onRename: (String, String) -> Unit,
    onDelete: (String) -> Unit,
    themeMode: String
) {
    val isLight = themeMode == "Light"
    
    val bgColor = if(isLight) Color(0xFFF5F7F8) else BgDark
    val textColor = if(isLight) Color(0xFF1F2937) else Color.White
    
    var showCreateDialog by remember { mutableStateOf(false) }
    var showRenameDialog by remember { mutableStateOf<String?>(null) } // Holds the name of profile to rename

    // Map strings to LayoutItems for display logic
    val displayLayouts = layouts.mapIndexed { index, name ->
        LayoutItem(
            title = name,
            lastUsed = stringResource(R.string.layouts_saved), // Placeholder for now
            isActive = name == activeProfile,
            gradientColors = if(index % 2 == 0) listOf(Color(0xFF8B5CF6), Color(0xFF3B82F6)) else listOf(Color(0xFF10B981), Color(0xFF064E3B))
        )
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(bgColor)
    ) {
        Column(modifier = Modifier.fillMaxSize()) {
            
            // Minimal Header (Back Button)
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    modifier = Modifier
                        .size(40.dp)
                        .clip(CircleShape)
                        .background(if(isLight) Color.White else Color(0xFF151921))
                        .clickable { onBack() }
                        .border(1.dp, if(isLight) Color(0xFFE5E7EB) else Color.White.copy(alpha = 0.1f), CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Filled.ArrowBack, 
                        contentDescription = stringResource(R.string.action_back), 
                        tint = if(isLight) Color.Black else Color.White
                    )
                }
            }
            
            // Grid Content
            LazyVerticalGrid(
                columns = GridCells.Fixed(1),
                contentPadding = PaddingValues(horizontal = 24.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
                modifier = Modifier.weight(1f).fillMaxWidth()
            ) {
                items(displayLayouts) { item ->
                    LayoutCard(
                        item = item, 
                        isLight = isLight,
                        onSelect = { onSelect(item.title) },
                        onEdit = { onEdit(item.title) },
                        onRename = { showRenameDialog = item.title },
                        onDelete = { onDelete(item.title) }
                    )
                }
                
                // Bottom Spacer
                item { Spacer(Modifier.height(80.dp)) }
            }
        }
        
        // Create New FAB
        Box(
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(24.dp)
                .zIndex(10f)
        ) {
            Button(
                onClick = { showCreateDialog = true },
                colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue),
                contentPadding = PaddingValues(horizontal = 20.dp, vertical = 12.dp),
                elevation = ButtonDefaults.buttonElevation(defaultElevation = 8.dp)
            ) {
                Icon(Icons.Filled.Add, null, modifier = Modifier.size(20.dp))
                Spacer(Modifier.width(8.dp))
                Text(stringResource(R.string.layouts_create), fontWeight = FontWeight.Bold)
            }
        }
        
        // Callback + state write must not happen during composition.
        LaunchedEffect(showCreateDialog) {
            if (showCreateDialog) {
                showCreateDialog = false
                onCreate()
            }
        }
        
        showRenameDialog?.let { oldName ->
            InputDialog(
                title = stringResource(R.string.layouts_rename_title),
                initialValue = oldName,
                isLight = isLight,
                onDismiss = { showRenameDialog = null },
                onConfirm = { newName ->
                    onRename(oldName, newName)
                    showRenameDialog = null
                }
            )
        }
    }
}

@Composable
fun LayoutCard(
    item: LayoutItem, 
    isLight: Boolean,
    onSelect: () -> Unit,
    onEdit: () -> Unit,
    onRename: () -> Unit,
    onDelete: () -> Unit
) {
    var showMenu by remember { mutableStateOf(false) }
    
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .height(90.dp)
            .clickable { onSelect() },
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(
            containerColor = if(isLight) Color.White else SurfaceDark
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxSize(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Thumbnail Area (Left)
            Box(
                modifier = Modifier
                    .width(90.dp)
                    .fillMaxHeight()
                    .background(
                        Brush.linearGradient(
                            colors = listOf(
                                item.gradientColors[0].copy(alpha = 0.6f),
                                item.gradientColors[1].copy(alpha = 0.2f)
                            )
                        )
                    )
            ) {
                // Overlay Pattern/Icon
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Icon(
                        if(item.title.contains("Racing")) Icons.Filled.Star else Icons.Filled.List,
                        null,
                        tint = Color.White.copy(alpha=0.2f),
                        modifier = Modifier.size(32.dp)
                    )
                }
            }
            
            // Content Area (Middle)
            Column(
                modifier = Modifier
                    .weight(1f)
                    .padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.Center
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        item.title,
                        color = if(isLight) Color(0xFF1F2937) else Color.White,
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        maxLines = 1
                    )
                    
                    if (item.isActive) {
                        Spacer(Modifier.width(8.dp))
                        Box(
                            modifier = Modifier
                                .background(PrimaryBlue.copy(alpha = 0.2f), RoundedCornerShape(4.dp))
                                .border(1.dp, PrimaryBlue.copy(alpha = 0.3f), RoundedCornerShape(4.dp))
                                .padding(horizontal = 6.dp, vertical = 2.dp)
                        ) {
                            Text(
                                stringResource(R.string.layouts_active),
                                color = PrimaryBlue,
                                fontSize = 10.sp,
                                fontWeight = FontWeight.Bold,
                                letterSpacing = 1.sp
                            )
                        }
                    }
                }
                
                Spacer(Modifier.height(4.dp))
                
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Schedule, null, tint = Color.Gray, modifier = Modifier.size(12.dp))
                    Spacer(Modifier.width(4.dp))
                    Text(
                        stringResource(R.string.layouts_last_used, item.lastUsed),
                        color = Color.Gray,
                        fontSize = 12.sp
                    )
                }
            }
            
            // Menu Button (Right)
            Box(
                modifier = Modifier
                    .padding(end = 12.dp)
                    .size(32.dp),
                contentAlignment = Alignment.Center
            ) {
                IconButton(onClick = { showMenu = true }) {
                    Icon(
                        Icons.Filled.MoreVert, 
                        null, 
                        tint = Color.Gray, 
                        modifier = Modifier.size(20.dp)
                    )
                }
                
                DropdownMenu(
                    expanded = showMenu,
                    onDismissRequest = { showMenu = false },
                    modifier = Modifier.background(if(isLight) Color.White else SurfaceDarker)
                ) {
                    DropdownMenuItem(
                        text = { Text(stringResource(R.string.layouts_open_designer), color = if(isLight) Color.Black else Color.White) },
                        onClick = { 
                            showMenu = false 
                            onEdit()
                        },
                        leadingIcon = { Icon(Icons.Filled.Edit, null, tint = if(isLight) Color.Gray else Color.White) }
                    )
                    DropdownMenuItem(
                        text = { Text(stringResource(R.string.layouts_rename), color = if(isLight) Color.Black else Color.White) },
                        onClick = { 
                            showMenu = false 
                            onRename()
                        },
                        leadingIcon = { Icon(Icons.Filled.List, null, tint = if(isLight) Color.Gray else Color.White) }
                    )
                    DropdownMenuItem(
                        text = { Text(stringResource(R.string.layouts_delete), color = Color.Red) },
                        onClick = { 
                            showMenu = false 
                            onDelete()
                        },
                        leadingIcon = { Icon(Icons.Filled.Delete, null, tint = Color.Red) }
                    )
                }
            }
        }
    }
}

@Composable
fun InputDialog(
    title: String,
    initialValue: String,
    isLight: Boolean,
    onDismiss: () -> Unit,
    onConfirm: (String) -> Unit
) {
    var text by remember { mutableStateOf(initialValue) }
    
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(title, color = if(isLight) Color.Black else Color.White)
        },
        text = {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                singleLine = true,
                colors = OutlinedTextFieldDefaults.colors(
                    focusedTextColor = if(isLight) Color.Black else Color.White,
                    unfocusedTextColor = if(isLight) Color.Black else Color.White,
                    focusedContainerColor = Color.Transparent,
                    unfocusedContainerColor = Color.Transparent,
                    focusedBorderColor = PrimaryBlue,
                    unfocusedBorderColor = if(isLight) Color.Gray else Color.White.copy(alpha=0.5f)
                ),
                modifier = Modifier.fillMaxWidth()
            )
        },
        confirmButton = {
            Button(
                onClick = { if(text.isNotBlank()) onConfirm(text) },
                colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue),
            ) {
                Text(stringResource(R.string.action_confirm), color = Color.White)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text(stringResource(R.string.action_cancel), color = if(isLight) Color.Gray else Color.White.copy(alpha=0.7f))
            }
        },
        containerColor = if(isLight) Color.White else SurfaceDark,
        tonalElevation = 8.dp
    )
}
