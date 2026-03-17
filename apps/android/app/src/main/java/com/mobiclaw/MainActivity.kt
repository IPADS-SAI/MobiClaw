package com.mobiclaw

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Rect
import android.os.Bundle
import android.provider.Settings
import android.view.MotionEvent
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import android.widget.TextView
import android.view.View
import android.widget.LinearLayout
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText

class MainActivity : AppCompatActivity() {

    private lateinit var settings: SettingsManager
    private var adbDiscovery: AdbPortDiscovery? = null

    private lateinit var etGatewayHost: TextInputEditText
    private lateinit var etGatewayPort: TextInputEditText
    private lateinit var etApiKey: TextInputEditText
    private lateinit var etManualAdbPort: TextInputEditText
    private lateinit var layoutManualAdb: LinearLayout
    private lateinit var btnSetClearAdb: MaterialButton
    private lateinit var tvAdbPort: TextView
    private lateinit var tvTailscaleIp: TextView
    private lateinit var tvStatus: TextView
    private lateinit var tvLastHeartbeat: TextView
    private lateinit var btnToggle: MaterialButton

    private var serviceRunning = false
    private var manualOverrideActive = false
    private val mdnsRefreshRunnable = Runnable { refreshMdnsLoop() }

    private val statusReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val status = intent.getStringExtra(HeartbeatService.EXTRA_STATUS) ?: return
            val message = intent.getStringExtra(HeartbeatService.EXTRA_MESSAGE) ?: ""
            val adbPort = intent.getIntExtra(HeartbeatService.EXTRA_ADB_PORT, 0)
            runOnUiThread { updateStatusDisplay(status, message, adbPort) }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        settings = SettingsManager(this)
        bindViews()
        loadSettings()
        refreshDeviceInfo()
        if (!manualOverrideActive) {
            startMdnsDetection()
        }

        btnToggle.setOnClickListener { toggleService() }
        btnSetClearAdb.setOnClickListener { onSetClearAdbClicked() }
    }

    override fun onResume() {
        super.onResume()
        val filter = IntentFilter(HeartbeatService.ACTION_STATUS_UPDATE)
        registerReceiver(statusReceiver, filter, RECEIVER_NOT_EXPORTED)
    }

    override fun onPause() {
        super.onPause()
        unregisterReceiver(statusReceiver)
        saveSettings()
    }

    override fun onDestroy() {
        tvAdbPort.removeCallbacks(mdnsRefreshRunnable)
        adbDiscovery?.stopDiscovery()
        super.onDestroy()
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        if (ev.action == MotionEvent.ACTION_DOWN) {
            val focused = currentFocus
            if (focused is EditText) {
                val rect = Rect()
                focused.getGlobalVisibleRect(rect)
                if (!rect.contains(ev.rawX.toInt(), ev.rawY.toInt())) {
                    focused.clearFocus()
                    val imm = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
                    imm.hideSoftInputFromWindow(focused.windowToken, 0)
                }
            }
        }
        return super.dispatchTouchEvent(ev)
    }

    private fun bindViews() {
        etGatewayHost = findViewById(R.id.etGatewayHost)
        etGatewayPort = findViewById(R.id.etGatewayPort)
        etApiKey = findViewById(R.id.etApiKey)
        etManualAdbPort = findViewById(R.id.etManualAdbPort)
        layoutManualAdb = findViewById(R.id.layoutManualAdb)
        btnSetClearAdb = findViewById(R.id.btnSetClearAdb)
        tvAdbPort = findViewById(R.id.tvAdbPort)
        tvTailscaleIp = findViewById(R.id.tvTailscaleIp)
        tvStatus = findViewById(R.id.tvStatus)
        tvLastHeartbeat = findViewById(R.id.tvLastHeartbeat)
        btnToggle = findViewById(R.id.btnToggle)
    }

    private fun loadSettings() {
        etGatewayHost.setText(settings.gatewayHost)
        etGatewayPort.setText(
            if (settings.gatewayPort != 0) settings.gatewayPort.toString() else "8090"
        )
        etApiKey.setText(settings.apiKey)
        val manualPort = settings.manualAdbPort
        if (manualPort > 0) {
            manualOverrideActive = true
            etManualAdbPort.setText(manualPort.toString())
        }
    }

    private fun saveSettings() {
        settings.gatewayHost = etGatewayHost.text.toString().trim()
        settings.gatewayPort = etGatewayPort.text.toString().trim().toIntOrNull() ?: 8090
        settings.apiKey = etApiKey.text.toString().trim()
    }

    private fun startMdnsDetection() {
        if (adbDiscovery == null) {
            adbDiscovery = AdbPortDiscovery(this)
        }
        adbDiscovery?.stopDiscovery()
        adbDiscovery?.startDiscovery()
        // First check after 1s for quick feedback, then periodic refresh
        tvAdbPort.removeCallbacks(mdnsRefreshRunnable)
        tvAdbPort.postDelayed(mdnsRefreshRunnable, 1000)
    }

    private fun refreshMdnsLoop() {
        if (manualOverrideActive) return
        // If wireless debugging was turned off, clear the stale port
        if (adbDiscovery?.port?.let { it > 0 } == true && !isWirelessDebuggingEnabled()) {
            adbDiscovery?.clearPort()
        }
        refreshDeviceInfo()
        tvAdbPort.postDelayed(mdnsRefreshRunnable, MDNS_REFRESH_INTERVAL_MS)
    }

    private fun isWirelessDebuggingEnabled(): Boolean {
        return try {
            Settings.Global.getInt(contentResolver, "adb_wifi_enabled", 0) == 1
        } catch (e: Exception) {
            true // assume enabled if we can't check
        }
    }

    private fun refreshDeviceInfo() {
        val savedManualPort = settings.manualAdbPort.takeIf { it > 0 }
        val mdnsPort = adbDiscovery?.port?.takeIf { it > 0 }
        val tailscaleIp = DeviceDetector.detectTailscaleIp()

        when {
            manualOverrideActive && savedManualPort != null -> {
                tvAdbPort.text = savedManualPort.toString()
                etManualAdbPort.setText(savedManualPort.toString())
                btnSetClearAdb.text = getString(R.string.btn_clear_adb_port)
                layoutManualAdb.visibility = View.VISIBLE
                adbDiscovery?.stopDiscovery()
            }
            mdnsPort != null -> {
                tvAdbPort.text = mdnsPort.toString()
                layoutManualAdb.visibility = View.GONE
            }
            else -> {
                tvAdbPort.text = getString(R.string.no_wireless_debug)
                etManualAdbPort.text?.clear()
                btnSetClearAdb.text = getString(R.string.btn_set_adb_port)
                layoutManualAdb.visibility = View.VISIBLE
            }
        }

        tvTailscaleIp.text = tailscaleIp ?: getString(R.string.not_detected)
    }

    private fun onSetClearAdbClicked() {
        if (manualOverrideActive) {
            // Clear
            settings.manualAdbPort = 0
            manualOverrideActive = false
            startMdnsDetection()
        } else {
            // Set
            val port = etManualAdbPort.text.toString().trim().toIntOrNull()
            if (port != null && port in 1..65535) {
                settings.manualAdbPort = port
                manualOverrideActive = true
                adbDiscovery?.stopDiscovery()
                refreshDeviceInfo()
            }
        }
    }

    private fun toggleService() {
        saveSettings()
        if (serviceRunning) {
            stopService(Intent(this, HeartbeatService::class.java))
            serviceRunning = false
            updateToggleButton()
            tvStatus.text = getString(R.string.status_stopped)
            tvStatus.setTextColor(ContextCompat.getColor(this, R.color.status_stopped))
        } else {
            val intent = Intent(this, HeartbeatService::class.java)
            startForegroundService(intent)
            serviceRunning = true
            updateToggleButton()
            tvStatus.text = getString(R.string.status_running)
            tvStatus.setTextColor(ContextCompat.getColor(this, R.color.status_running))
        }
    }

    private fun updateToggleButton() {
        btnToggle.text = if (serviceRunning) {
            getString(R.string.btn_disconnect)
        } else {
            getString(R.string.btn_connect)
        }
    }

    private fun updateStatusDisplay(status: String, message: String, adbPort: Int) {
        when (status) {
            HeartbeatService.STATUS_RUNNING -> {
                tvStatus.text = getString(R.string.status_running)
                tvStatus.setTextColor(ContextCompat.getColor(this, R.color.status_running))
                tvLastHeartbeat.text = message
                if (adbPort > 0) {
                    tvAdbPort.text = adbPort.toString()
                }
            }
            HeartbeatService.STATUS_ERROR -> {
                tvStatus.text = getString(R.string.status_error)
                tvStatus.setTextColor(ContextCompat.getColor(this, R.color.status_error))
                tvLastHeartbeat.text = message
            }
            HeartbeatService.STATUS_DISCONNECTED -> {
                serviceRunning = false
                updateToggleButton()
                tvStatus.text = getString(R.string.status_stopped)
                tvStatus.setTextColor(ContextCompat.getColor(this, R.color.status_stopped))
                tvLastHeartbeat.text = message
            }
        }
    }

    companion object {
        private const val REQ_NOTIFICATION = 1001
        private const val MDNS_REFRESH_INTERVAL_MS = 5000L
    }
}
