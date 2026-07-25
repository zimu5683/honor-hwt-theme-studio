package io.github.zimu5683.hwttransfer

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class ProtocolTest {
    @Test
    fun safeFileNameRemovesTraversalAndKeepsChinese() {
        assertEquals("我的_主题.hwt", Protocol.safeFileName("../我的 主题.hwt"))
        assertEquals("theme.hwt", Protocol.safeFileName("theme"))
    }

    @Test
    fun validateHwtRequiresDescription() {
        val file = File.createTempFile("invalid", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("wallpaper/test.jpg"))
                zip.write(byteArrayOf(1, 2, 3))
                zip.closeEntry()
            }
            assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtAcceptsThemeSkeleton() {
        val file = File.createTempFile("valid", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
            }
            Protocol.validateHwt(file)
        } finally {
            file.delete()
        }
    }
}
