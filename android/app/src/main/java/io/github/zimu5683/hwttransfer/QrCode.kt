package io.github.zimu5683.hwttransfer

import android.graphics.Bitmap
import android.graphics.Color
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter

internal fun connectUrl(host: String, port: Int = Protocol.HTTP_PORT): String {
    val wrapped = if (host.contains(":")) "[$host]" else host
    return "hwtstudio://$wrapped:$port"
}

/**
 * 生成连接二维码的位图（内容为 hwtstudio://IP:端口），
 * 供电脑端扫码后免手动输入地址。
 */
fun qrBitmap(content: String, size: Int = 512): Bitmap? {
    if (content.isBlank()) return null
    return try {
        val hints = mapOf(EncodeHintType.MARGIN to 1)
        val matrix = QRCodeWriter().encode(content, BarcodeFormat.QR_CODE, size, size, hints)
        Bitmap.createBitmap(size, size, Bitmap.Config.RGB_565).apply {
            for (x in 0 until size) {
                for (y in 0 until size) {
                    setPixel(x, y, if (matrix.get(x, y)) Color.BLACK else Color.WHITE)
                }
            }
        }
    } catch (_: Exception) {
        null
    }
}
