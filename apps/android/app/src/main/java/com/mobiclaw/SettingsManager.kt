package com.mobiclaw

import android.content.Context
import android.content.SharedPreferences

class SettingsManager(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences("seneschal_heartbeat", Context.MODE_PRIVATE)

    var gatewayHost: String
        get() = prefs.getString(KEY_GATEWAY_HOST, "") ?: ""
        set(value) = prefs.edit().putString(KEY_GATEWAY_HOST, value).apply()

    var gatewayPort: Int
        get() = prefs.getInt(KEY_GATEWAY_PORT, 8090)
        set(value) = prefs.edit().putInt(KEY_GATEWAY_PORT, value).apply()

    var apiKey: String
        get() = prefs.getString(KEY_API_KEY, "") ?: ""
        set(value) = prefs.edit().putString(KEY_API_KEY, value).apply()

    var heartbeatIntervalSec: Int
        get() = prefs.getInt(KEY_HEARTBEAT_INTERVAL, 30)
        set(value) = prefs.edit().putInt(KEY_HEARTBEAT_INTERVAL, value).apply()

    /** Manual ADB port override. 0 means auto-detect. */
    var manualAdbPort: Int
        get() = prefs.getInt(KEY_MANUAL_ADB_PORT, 0)
        set(value) = prefs.edit().putInt(KEY_MANUAL_ADB_PORT, value).apply()

    companion object {
        private const val KEY_GATEWAY_HOST = "gateway_host"
        private const val KEY_GATEWAY_PORT = "gateway_port"
        private const val KEY_API_KEY = "api_key"
        private const val KEY_HEARTBEAT_INTERVAL = "heartbeat_interval"
        private const val KEY_MANUAL_ADB_PORT = "manual_adb_port"
    }
}
