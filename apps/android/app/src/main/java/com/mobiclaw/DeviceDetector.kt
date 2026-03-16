package com.mobiclaw

import android.os.Build
import java.net.Inet4Address
import java.net.NetworkInterface

object DeviceDetector {

    /**
     * Detect the Tailscale IP address by scanning network interfaces
     * for an IPv4 address in the 100.64.0.0/10 range (CGNAT range used by Tailscale).
     */
    fun detectTailscaleIp(): String? {
        return try {
            val interfaces = NetworkInterface.getNetworkInterfaces() ?: return null
            for (iface in interfaces) {
                for (addr in iface.inetAddresses) {
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        val ip = addr.address
                        // 100.64.0.0/10: first byte == 100, second byte in 64..127
                        if (ip[0].toInt() and 0xFF == 100 &&
                            ip[1].toInt() and 0xFF in 64..127
                        ) {
                            return addr.hostAddress
                        }
                    }
                }
            }
            null
        } catch (_: Exception) {
            null
        }
    }

    /** Returns the device model name (e.g., "Pixel 8"). */
    fun getDeviceName(): String = Build.MODEL
}
