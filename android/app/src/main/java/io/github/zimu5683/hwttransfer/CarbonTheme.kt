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
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.unit.dp

private val CarbonBlue = Color(0xFF0F62FE)
private val CarbonBlue60 = Color(0xFF0043CE)
private val CarbonBlue80 = Color(0xFF002D9C)
private val CarbonInk = Color(0xFF161616)
private val CarbonInkMuted = Color(0xFF525252)
private val CarbonInkSubtle = Color(0xFF8C8C8C)
private val CarbonCanvas = Color(0xFFFFFFFF)
private val CarbonSurface1 = Color(0xFFF4F4F4)
private val CarbonSurface2 = Color(0xFFE0E0E0)
private val CarbonHairline = Color(0xFFE0E0E0)
private val CarbonSuccess = Color(0xFF24A148)
private val CarbonWarning = Color(0xFFF1C21B)
private val CarbonError = Color(0xFFDA1E28)

private val CarbonFont = FontFamily(
    Font(R.font.ibm_plex_sans_sc_light, FontWeight.Light),
    Font(R.font.ibm_plex_sans_sc_regular, FontWeight.Normal),
    Font(R.font.ibm_plex_sans_sc_semibold, FontWeight.SemiBold),
)

val CarbonLightColorScheme: ColorScheme = lightColorScheme(
    primary = CarbonBlue,
    onPrimary = CarbonCanvas,
    primaryContainer = CarbonSurface1,
    onPrimaryContainer = CarbonInk,
    secondary = CarbonInk,
    onSecondary = CarbonCanvas,
    secondaryContainer = CarbonSurface1,
    onSecondaryContainer = CarbonInk,
    tertiary = CarbonBlue60,
    onTertiary = CarbonCanvas,
    background = CarbonCanvas,
    onBackground = CarbonInk,
    surface = CarbonCanvas,
    onSurface = CarbonInk,
    surfaceVariant = CarbonSurface1,
    onSurfaceVariant = CarbonInkMuted,
    outline = CarbonHairline,
    outlineVariant = CarbonSurface2,
    error = CarbonError,
    onError = CarbonCanvas,
    errorContainer = CarbonSurface1,
    onErrorContainer = CarbonError,
)

val CarbonLightTypography = Typography(
    displayLarge = TextStyle(fontFamily = CarbonFont, fontSize = 42.sp, lineHeight = 50.4.sp, fontWeight = FontWeight.Light),
    headlineLarge = TextStyle(fontFamily = CarbonFont, fontSize = 32.sp, lineHeight = 40.sp, fontWeight = FontWeight.Normal),
    headlineMedium = TextStyle(fontFamily = CarbonFont, fontSize = 24.sp, lineHeight = 32.sp, fontWeight = FontWeight.Normal),
    titleLarge = TextStyle(fontFamily = CarbonFont, fontSize = 20.sp, lineHeight = 28.sp, fontWeight = FontWeight.Normal),
    bodyLarge = TextStyle(fontFamily = CarbonFont, fontSize = 16.sp, lineHeight = 24.sp, fontWeight = FontWeight.Normal, letterSpacing = 0.16.sp),
    bodyMedium = TextStyle(fontFamily = CarbonFont, fontSize = 14.sp, lineHeight = 18.sp, fontWeight = FontWeight.Normal, letterSpacing = 0.16.sp),
    bodySmall = TextStyle(fontFamily = CarbonFont, fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Normal, letterSpacing = 0.32.sp),
    labelLarge = TextStyle(fontFamily = CarbonFont, fontSize = 14.sp, lineHeight = 18.sp, fontWeight = FontWeight.Normal, letterSpacing = 0.16.sp),
)

/** Carbon keeps app surfaces square; Material's default 4/12/28dp corners are intentionally removed. */
val CarbonShapes = Shapes(
    extraSmall = RoundedCornerShape(0.dp),
    small = RoundedCornerShape(0.dp),
    medium = RoundedCornerShape(0.dp),
    large = RoundedCornerShape(0.dp),
    extraLarge = RoundedCornerShape(0.dp),
)

@Composable
fun CarbonTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = CarbonLightColorScheme,
        typography = CarbonLightTypography,
        shapes = CarbonShapes,
        content = content,
    )
}

object CarbonSemanticColors {
    val success: Color = CarbonSuccess
    val warning: Color = CarbonWarning
    val error: Color = CarbonError
    val muted: Color = CarbonInkMuted
    val subtle: Color = CarbonInkSubtle
    val surface1: Color = CarbonSurface1
    val surface2: Color = CarbonSurface2
    val hairline: Color = CarbonHairline
    val blue80: Color = CarbonBlue80
}
