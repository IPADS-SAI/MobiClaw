package com.mobiclaw

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import java.text.SimpleDateFormat
import java.util.*

class HeartbeatService : Service() {

    private lateinit var settings: SettingsManager
    private lateinit var adbDiscovery: AdbPortDiscovery
    private val client = HeartbeatClient()
    private var wakeLock: PowerManager.WakeLock? = null
    private var handlerThread: HandlerThread? = null
    private var handler: Handler? = null
    private var running = false
    private var everHadPort = false
    /** Port that last succeeded; avoids mDNS race where another device's resolve overwrites ours. */
    private var lastSuccessfulPort: Int = 0

    private val tickRunnable = object : Runnable {
        override fun run() {
            if (!running) return
            val nextDelayMs = performHeartbeat()
            handler?.postDelayed(this, nextDelayMs)
        }
    }

    override fun onCreate() {
        super.onCreate()
        settings = SettingsManager(this)
        adbDiscovery = AdbPortDiscovery(this)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIFICATION_ID,
                buildNotification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
            )
        } else {
            startForeground(NOTIFICATION_ID, buildNotification())
        }
        acquireWakeLock()
        adbDiscovery.startDiscovery()
        startHeartbeatLoop()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        unregisterFromGateway()
        stopHeartbeatLoop()
        adbDiscovery.stopDiscovery()
        releaseWakeLock()
        super.onDestroy()
    }

    private fun unregisterFromGateway() {
        val host = settings.gatewayHost
        if (host.isBlank()) return
        val baseUrl = "http://$host:${settings.gatewayPort}"
        val deviceId = Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID)
        client.unregisterDevice(baseUrl, settings.apiKey, deviceId)
    }

    private fun acquireWakeLock() {
        if (wakeLock == null) {
            val pm = getSystemService(POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "$TAG:heartbeat")
        }
        wakeLock?.takeIf { !it.isHeld }?.acquire()
    }

    private fun releaseWakeLock() {
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
    }

    private fun startHeartbeatLoop() {
        if (running) return
        running = true
        handlerThread = HandlerThread("HeartbeatThread").also { it.start() }
        handler = Handler(handlerThread!!.looper)
        handler?.post(tickRunnable)
        Log.i(TAG, "Heartbeat loop started (interval=${settings.heartbeatIntervalSec}s)")
    }

    private fun stopHeartbeatLoop() {
        running = false
        handler?.removeCallbacks(tickRunnable)
        handlerThread?.quitSafely()
        handlerThread = null
        handler = null
        Log.i(TAG, "Heartbeat loop stopped")
    }

    /** Returns delay in ms until next tick. */
    private fun performHeartbeat(): Long {
        // Priority: manual > lastSuccessfulPort (avoids mDNS race) > mDNS
        val manualPort = settings.manualAdbPort.takeIf { it > 0 }
        val mdnsPort = adbDiscovery.port.takeIf { it > 0 }
        val adbPort = manualPort
            ?: lastSuccessfulPort.takeIf { it > 0 }
            ?: mdnsPort

        Log.d(TAG, "ADB port: manual=$manualPort lastOk=$lastSuccessfulPort mdns=$mdnsPort -> using=$adbPort")

        if (adbPort == null) {
            if (everHadPort) {
                Log.w(TAG, "ADB port lost, auto-disconnecting")
                broadcastStatus(STATUS_DISCONNECTED, "ADB port lost")
                stopSelf()
            } else {
                Log.d(TAG, "ADB port not yet discovered, waiting...")
            }
            return PORT_DISCOVERY_RETRY_MS
        }

        everHadPort = true

        val tailscaleIp = DeviceDetector.detectTailscaleIp()
        val deviceId = Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID)

        val host = settings.gatewayHost
        val port = settings.gatewayPort
        if (host.isBlank()) {
            Log.w(TAG, "Gateway host not configured, skipping heartbeat")
            broadcastStatus(STATUS_ERROR, "Gateway host not configured")
            return settings.heartbeatIntervalSec * 1000L
        }

        val baseUrl = "http://$host:$port"
        val payload = mapOf<String, Any?>(
            "device_id" to deviceId,
            "tailscale_ip" to tailscaleIp,
            "adb_port" to adbPort,
            "device_name" to DeviceDetector.getDeviceName(),
        )

        client.sendHeartbeat(
            baseUrl = baseUrl,
            apiKey = settings.apiKey,
            payload = payload,
            onSuccess = { _ ->
                lastSuccessfulPort = adbPort
                val time = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
                broadcastStatus(STATUS_RUNNING, time, adbPort)
            },
            onError = { msg ->
                lastSuccessfulPort = 0
                broadcastStatus(STATUS_ERROR, msg, adbPort)
            },
        )
        return settings.heartbeatIntervalSec * 1000L
    }

    private fun broadcastStatus(statusCode: String, message: String, adbPort: Int? = null) {
        val intent = Intent(ACTION_STATUS_UPDATE).apply {
            setPackage(packageName)
            putExtra(EXTRA_STATUS, statusCode)
            putExtra(EXTRA_MESSAGE, message)
            putExtra(EXTRA_ADB_PORT, adbPort ?: 0)
        }
        sendBroadcast(intent)
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(getString(R.string.notification_text))
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val TAG = "HeartbeatService"
        private const val CHANNEL_ID = "heartbeat_channel"
        private const val NOTIFICATION_ID = 1
        /** Retry interval when waiting for mDNS to discover ADB port. */
        private const val PORT_DISCOVERY_RETRY_MS = 2000L

        const val ACTION_STATUS_UPDATE = "com.mobiclaw.STATUS_UPDATE"
        const val EXTRA_STATUS = "status"
        const val EXTRA_MESSAGE = "message"
        const val EXTRA_ADB_PORT = "adb_port"
        const val STATUS_RUNNING = "running"
        const val STATUS_ERROR = "error"
        const val STATUS_DISCONNECTED = "disconnected"
    }
}
