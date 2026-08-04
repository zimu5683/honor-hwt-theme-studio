package io.github.zimu5683.hwttransfer

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject

object DeviceProfile {
    val targetPackages = sortedSetOf(
        "com.android.contacts", "com.android.deskclock", "com.android.documentsui", "com.android.mms",
        "com.android.packageinstaller", "com.android.server.telecom", "com.android.settings",
        "com.android.soundrecorder", "com.android.systemui", "com.hihonor.KoBackup",
        "com.hihonor.android.dsdscardmanager", "com.hihonor.android.instantshare",
        "com.hihonor.android.internal.app", "com.hihonor.android.launcher",
        "com.hihonor.android.thememanager", "com.hihonor.android.totemweather", "com.hihonor.appmarket",
        "com.hihonor.calculator", "com.hihonor.calendar", "com.hihonor.contacts", "com.hihonor.deskclock",
        "com.hihonor.devicemanager", "com.hihonor.filemanager", "com.hihonor.gameassistant",
        "com.hihonor.hidisk", "com.hihonor.hndockbar", "com.hihonor.hwvoipservice",
        "com.hihonor.intelligent", "com.hihonor.mms", "com.hihonor.motionservice", "com.hihonor.notepad",
        "com.hihonor.ouc", "com.hihonor.phone", "com.hihonor.phone.recorder", "com.hihonor.phoneservice",
        "com.hihonor.photos", "com.hihonor.scanner", "com.hihonor.smarthome",
        "com.hihonor.soundrecorder", "com.hihonor.systemmanager", "com.hihonor.wallet",
        "com.huawei.KoBackup", "com.huawei.android.dsdscardmanager", "com.huawei.android.hwouc",
        "com.huawei.android.instantshare", "com.huawei.android.internal.app", "com.huawei.android.launcher",
        "com.huawei.android.thememanager", "com.huawei.android.totemweather", "com.huawei.appmarket",
        "com.huawei.browser", "com.huawei.calculator", "com.huawei.calendar", "com.huawei.contacts",
        "com.huawei.deskclock", "com.huawei.filemanager", "com.huawei.gameassistant", "com.huawei.hidisk",
        "com.huawei.hwdockbar", "com.huawei.hwvoipservice", "com.huawei.meetime", "com.huawei.mms",
        "com.huawei.motionservice", "com.huawei.notepad", "com.huawei.phone", "com.huawei.phoneservice",
        "com.huawei.photos", "com.huawei.scanner", "com.huawei.smarthome", "com.huawei.soundrecorder",
        "com.huawei.systemmanager", "com.huawei.wallet", "com.tencent.mm",
    )

    fun json(context: Context): JSONObject {
        val installed = targetPackages.filter { isInstalled(context.packageManager, it) }
        return JSONObject()
            .put("manufacturer", Build.MANUFACTURER)
            .put("model", Build.MODEL)
            .put("android_release", Build.VERSION.RELEASE)
            .put("sdk_int", Build.VERSION.SDK_INT)
            .put("os_name", systemProperty("ro.build.version.magic"))
            .put("build_display", Build.DISPLAY)
            .put("installed_packages", JSONArray(installed))
    }

    private fun isInstalled(manager: PackageManager, name: String): Boolean = runCatching {
        manager.getPackageInfo(name, PackageManager.PackageInfoFlags.of(0))
    }.isSuccess

    private fun systemProperty(name: String): String = runCatching {
        val process = ProcessBuilder("/system/bin/getprop", name).start()
        try {
            process.inputStream.bufferedReader().use { it.readLine().orEmpty() }
        } finally {
            process.destroy()
            if (process.isAlive) process.destroyForcibly()
        }
    }.getOrDefault("")
}
