package com.mobiclaw

import android.util.Log
import com.google.gson.Gson
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

class HeartbeatClient {

    private val client = OkHttpClient()
    private val gson = Gson()
    private val jsonType = "application/json; charset=utf-8".toMediaType()

    /**
     * Send a heartbeat POST to the gateway server.
     * @param baseUrl e.g. "http://192.168.1.100:8090"
     * @param apiKey Bearer token
     * @param payload map with device_id, tailscale_domain, tailscale_ip, adb_port, device_name
     * @param onSuccess called with response body on 2xx
     * @param onError called with error message on failure
     */
    fun sendHeartbeat(
        baseUrl: String,
        apiKey: String,
        payload: Map<String, Any?>,
        onSuccess: (String) -> Unit,
        onError: (String) -> Unit,
    ) {
        val url = "${baseUrl.trimEnd('/')}/api/v1/devices/heartbeat"
        val body = gson.toJson(payload).toRequestBody(jsonType)

        val requestBuilder = Request.Builder()
            .url(url)
            .post(body)
        if (apiKey.isNotBlank()) {
            requestBuilder.addHeader("Authorization", "Bearer $apiKey")
        }

        client.newCall(requestBuilder.build()).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                Log.e(TAG, "Heartbeat request failed", e)
                onError(e.message ?: "Network error")
            }

            override fun onResponse(call: Call, response: Response) {
                val responseBody = response.body?.string() ?: ""
                if (response.isSuccessful) {
                    Log.i(TAG, "Heartbeat sent: $responseBody")
                    onSuccess(responseBody)
                } else {
                    val msg = "HTTP ${response.code}: $responseBody"
                    Log.e(TAG, "Heartbeat error: $msg")
                    onError(msg)
                }
            }
        })
    }

    /**
     * Unregister this device from the gateway server (fire-and-forget).
     */
    fun unregisterDevice(baseUrl: String, apiKey: String, deviceId: String) {
        val url = "${baseUrl.trimEnd('/')}/api/v1/devices/$deviceId"

        val requestBuilder = Request.Builder()
            .url(url)
            .delete()
        if (apiKey.isNotBlank()) {
            requestBuilder.addHeader("Authorization", "Bearer $apiKey")
        }

        client.newCall(requestBuilder.build()).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                Log.e(TAG, "Unregister request failed", e)
            }

            override fun onResponse(call: Call, response: Response) {
                response.body?.close()
                if (response.isSuccessful) {
                    Log.i(TAG, "Device unregistered: $deviceId")
                } else {
                    Log.e(TAG, "Unregister error: HTTP ${response.code}")
                }
            }
        })
    }

    companion object {
        private const val TAG = "HeartbeatClient"
    }
}
