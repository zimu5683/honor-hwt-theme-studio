package io.github.zimu5683.hwttransfer

import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.shape.RoundedCornerShape

private val StudioPurple = Color(0xFF5645D4)
private val StudioPurplePressed = Color(0xFF4534B3)
private val StudioPurpleDeep = Color(0xFF3A2A99)
private val StudioLinkBlue = Color(0xFF0075DE)
private val StudioInk = Color(0xFF1A1A1A)
private val StudioCharcoal = Color(0xFF37352F)
private val StudioSlate = Color(0xFF5D5B54)
private val StudioSteel = Color(0xFF787671)
private val StudioCanvas = Color(0xFFF6F5F4)
private val StudioSurface = Color(0xFFFFFFFF)
private val StudioSurfaceSoft = Color(0xFFFAFAF9)
private val StudioHairline = Color(0xFFE5E3DF)
private val StudioHairlineStrong = Color(0xFFC8C4BE)
private val StudioSurfaceMuted = Color(0xFFF0EEEC)
private val StudioSuccess = Color(0xFF1AAE39)
private val StudioWarning = Color(0xFFDD5B00)
private val StudioError = Color(0xFFE03131)
private val StudioLavender = Color(0xFFE6E0F5)
private val StudioCream = Color(0xFFF8F5E8)
private val StudioMint = Color(0xFFD9F3E1)
private val StudioSky = Color(0xFFDCEFFA)
private val StudioPeach = Color(0xFFFFE8D4)
private val StudioRose = Color(0xFFFDE0EC)

private val StudioFont = FontFamily(
    Font(R.font.inter_variable, FontWeight.Normal),
)

val StudioLightColorScheme: ColorScheme = lightColorScheme(
    primary = StudioPurple,
    onPrimary = StudioSurface,
    primaryContainer = StudioLavender,
    onPrimaryContainer = StudioPurpleDeep,
    secondary = StudioCharcoal,
    onSecondary = StudioSurface,
    secondaryContainer = StudioSurfaceMuted,
    onSecondaryContainer = StudioInk,
    tertiary = StudioLinkBlue,
    onTertiary = StudioSurface,
    background = StudioCanvas,
    onBackground = StudioInk,
    surface = StudioSurface,
    onSurface = StudioInk,
    surfaceVariant = StudioSurfaceSoft,
    onSurfaceVariant = StudioSlate,
    outline = StudioHairlineStrong,
    outlineVariant = StudioHairline,
    error = StudioError,
    onError = StudioSurface,
    errorContainer = StudioRose,
    onErrorContainer = StudioError,
)

val StudioLightTypography = Typography(
    displayLarge = TextStyle(fontFamily = StudioFont, fontSize = 36.sp, lineHeight = 43.sp, fontWeight = FontWeight.SemiBold),
    headlineLarge = TextStyle(fontFamily = StudioFont, fontSize = 30.sp, lineHeight = 38.sp, fontWeight = FontWeight.SemiBold),
    headlineMedium = TextStyle(fontFamily = StudioFont, fontSize = 24.sp, lineHeight = 32.sp, fontWeight = FontWeight.SemiBold),
    titleLarge = TextStyle(fontFamily = StudioFont, fontSize = 20.sp, lineHeight = 28.sp, fontWeight = FontWeight.SemiBold),
    bodyLarge = TextStyle(fontFamily = StudioFont, fontSize = 16.sp, lineHeight = 25.sp, fontWeight = FontWeight.Normal),
    bodyMedium = TextStyle(fontFamily = StudioFont, fontSize = 14.sp, lineHeight = 21.sp, fontWeight = FontWeight.Normal),
    bodySmall = TextStyle(fontFamily = StudioFont, fontSize = 12.sp, lineHeight = 17.sp, fontWeight = FontWeight.Normal),
    labelLarge = TextStyle(fontFamily = StudioFont, fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.Medium),
)

val StudioShapes = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(8.dp),
    medium = RoundedCornerShape(8.dp),
    large = RoundedCornerShape(12.dp),
    extraLarge = RoundedCornerShape(16.dp),
)

@Composable
fun StudioSoftTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = StudioLightColorScheme,
        typography = StudioLightTypography,
        shapes = StudioShapes,
        content = content,
    )
}

object StudioSemanticColors {
    val success: Color = StudioSuccess
    val warning: Color = StudioWarning
    val error: Color = StudioError
    val muted: Color = StudioSlate
    val subtle: Color = StudioSteel
    val surface1: Color = StudioSurface
    val surface2: Color = StudioSurfaceSoft
    val surfaceMuted: Color = StudioSurfaceMuted
    val hairline: Color = StudioHairline
    val hairlineStrong: Color = StudioHairlineStrong
    val lavender: Color = StudioLavender
    val cream: Color = StudioCream
    val mint: Color = StudioMint
    val sky: Color = StudioSky
    val peach: Color = StudioPeach
    val rose: Color = StudioRose
    val purpleDeep: Color = StudioPurpleDeep
    val purplePressed: Color = StudioPurplePressed
}
