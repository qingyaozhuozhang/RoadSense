package com.roadsense.inspection

import android.Manifest
import android.app.*
import android.content.*
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.amap.api.location.AMapLocationClient
import com.amap.api.location.AMapLocationClientOption
import org.json.JSONObject
import android.webkit.CookieManager
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/** Foreground service continues while app is backgrounded or screen is off. */
class LocationUploadService : Service() {
    companion object {
        const val ACTION_START = "com.roadsense.inspection.START"
        const val ACTION_STOP = "com.roadsense.inspection.STOP"
        const val ACTION_UPDATE = "com.roadsense.inspection.LOCATION_UPDATE"
        const val EXTRA_PAYLOAD = "payload"
        const val EXTRA_DEVICE_ID = "device_id"
        const val NOTIFICATION_CHANNEL = "roadsense_location"
        @Volatile var running = false
    }
    private data class Fix(val latitude: Double, val longitude: Double,
                           val accuracy: Float, val capturedAt: Long)
    @Volatile private var lastFix: Fix? = null
    private var client: AMapLocationClient? = null
    private var uploader: ScheduledExecutorService? = null
    private var deviceId: String = ""

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { stopSelf(); return START_NOT_STICKY }
        val requestedDevice = intent?.getStringExtra(EXTRA_DEVICE_ID)?.trim().orEmpty()
        if (!running) { deviceId=requestedDevice;startTracking() }
        else if (deviceId!=requestedDevice) { stopSelf() }
        return START_NOT_STICKY // Never restart silently without a user's action.
    }

    private fun startTracking() {
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            stopSelf(); return
        }
        createChannel()
        val notification = NotificationCompat.Builder(this, NOTIFICATION_CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("道路智能巡检平台 · 正在定位")
            .setContentText("点击应用内的停止定位即可结束")
            .setOngoing(true)
            .setContentIntent(PendingIntent.getActivity(this, 0,
                Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
            .setPriority(NotificationCompat.PRIORITY_LOW).build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(102, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
        } else startForeground(102, notification)
        try {
            // Consent was obtained on the app's first launch. SDK may only initialize after consent.
            AMapLocationClient.updatePrivacyShow(this, true, true)
            AMapLocationClient.updatePrivacyAgree(this, true)
            val location = AMapLocationClient(applicationContext)
            val options = AMapLocationClientOption()
            options.setLocationMode(AMapLocationClientOption.AMapLocationMode.Hight_Accuracy)
            options.setInterval(1000L) // AMap SDK minimum interval is 1000 ms.
            options.setOnceLocation(false)
            options.setNeedAddress(false)
            options.setLocationCacheEnable(false)
            // The existing cloud/UI expect WGS84; never double-convert GCJ-02.
            options.setOffset(false)
            location.setLocationOption(options)
            location.setLocationListener { result ->
                if (result != null && result.errorCode == 0 &&
                    result.latitude in -90.0..90.0 && result.longitude in -180.0..180.0 &&
                    result.accuracy in 0f..100f) {
                    lastFix = Fix(result.latitude, result.longitude, result.accuracy, System.currentTimeMillis())
                    broadcast(JSONObject().put("latitude", result.latitude)
                        .put("longitude", result.longitude)
                        .put("accuracy", result.accuracy.toDouble())
                        .put("state", "定位成功，等待上传").toString())
                } else if (result != null && result.errorCode != 0) {
                    broadcast(JSONObject().put("state", "定位失败: ${result.errorCode} ${result.errorInfo}").toString())
                }
            }
            client = location
            running = true
            location.startLocation()
            uploader = Executors.newSingleThreadScheduledExecutor().also { pool ->
                pool.scheduleWithFixedDelay({ sendLatest() }, 0, 500, TimeUnit.MILLISECONDS)
            }
        } catch (e: Exception) {
            broadcast(JSONObject().put("state", "启动失败: ${e.message}").toString())
            stopSelf()
        }
    }

    private fun sendLatest() {
        val fix = lastFix ?: return
        if (System.currentTimeMillis() - fix.capturedAt > 10000L) return // stale fix
        var connection: HttpURLConnection? = null
        try {
            val body = JSONObject().put("latitude", fix.latitude)
                .put("longitude", fix.longitude).put("accuracy", fix.accuracy.toDouble())
                .apply { if(deviceId.isNotBlank())put("device_id",deviceId) }.toString()
            connection = (URL("${BuildConfig.CLOUD_BASE_URL}/api/receive_location").openConnection() as HttpURLConnection)
            connection.requestMethod = "POST"
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            // Use logged-in WebView session: cloud attributes this fix to the
            // account's assigned device instead of accepting anonymous uploads.
            val cookie = CookieManager.getInstance().getCookie(BuildConfig.CLOUD_BASE_URL)
            if (!cookie.isNullOrBlank()) connection.setRequestProperty("Cookie", cookie)
            connection.setRequestProperty("X-RoadSense-Mobile", "android")
            connection.connectTimeout = 2000
            connection.readTimeout = 2000
            connection.doOutput = true
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            val code = connection.responseCode
            if (code == 200) {
                broadcast(JSONObject().put("latitude", fix.latitude).put("longitude", fix.longitude)
                    .put("accuracy", fix.accuracy.toDouble()).put("state", "定位已同步到当前账号")
                    .put("uploaded", true).toString())
            } else if(code == 403 || code == 401) {
                broadcast(JSONObject().put("state", if(code==403)
                    "当前账号暂无定位权限，请联系管理员开通" else "登录已失效，请重新登录").toString())
                stopSelf()
            } else broadcast(JSONObject().put("state", "云端同步失败，请稍后重试").toString())
        } catch (e: Exception) {
            broadcast(JSONObject().put("state", "通信失败: ${e.javaClass.simpleName}").toString())
        } finally { connection?.disconnect() }
    }

    private fun broadcast(json: String) {
        // Internal package-scoped broadcast. Browser JS never receives native privileges.
        sendBroadcast(Intent(ACTION_UPDATE).setPackage(packageName).putExtra(EXTRA_PAYLOAD, json))
    }
    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(NOTIFICATION_CHANNEL,
                "道路智能巡检平台 · 定位服务", NotificationManager.IMPORTANCE_LOW))
        }
    }
    override fun onTaskRemoved(rootIntent: Intent?) {
        // Swiping RoadSense away from Recents stops the location foreground service.
        stopSelf()
        super.onTaskRemoved(rootIntent)
    }
    override fun onDestroy() {
        running = false
        uploader?.shutdownNow(); uploader = null
        try { client?.stopLocation(); client?.onDestroy() } catch (_: Exception) {}
        client = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }
}
