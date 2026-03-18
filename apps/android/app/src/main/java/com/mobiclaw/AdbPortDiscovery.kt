package com.mobiclaw

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.util.Log
import java.net.NetworkInterface
import java.util.concurrent.atomic.AtomicInteger

/**
 * Uses NsdManager (mDNS) to discover ADB wireless debugging port.
 * Android 11+ broadcasts the service as `_adb-tls-connect._tcp.` on the local network.
 */
class AdbPortDiscovery(context: Context) {

    private val nsdManager = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val discoveredPort = AtomicInteger(0)
    private var discovering = false

    /** The most recently discovered ADB port, or 0 if none found. */
    val port: Int get() = discoveredPort.get()

    private val discoveryListener = object : NsdManager.DiscoveryListener {
        override fun onDiscoveryStarted(serviceType: String) {
            Log.d(TAG, "mDNS discovery started for $serviceType")
        }

        override fun onServiceFound(serviceInfo: NsdServiceInfo) {
            Log.d(TAG, "mDNS service found: ${serviceInfo.serviceName} type=${serviceInfo.serviceType}")
            val listener = object : NsdManager.ResolveListener {
                override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                    val p = serviceInfo.port
                    val host = serviceInfo.host
                    Log.i(TAG, "mDNS resolved ADB port: $p (host=$host)")
                    if (host == null) {
                        Log.d(TAG, "Ignoring ADB service with null host (cannot verify local)")
                        return
                    }
                    if (!isLocalAddress(host)) {
                        Log.d(TAG, "Ignoring ADB service from non-local host: $host")
                        return
                    }
                    discoveredPort.set(p)
                }

                override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
                    Log.e(TAG, "mDNS resolve failed: ${serviceInfo.serviceName} error=$errorCode")
                }
            }
            try {
                nsdManager.resolveService(serviceInfo, listener)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to resolve service", e)
            }
        }

        override fun onServiceLost(serviceInfo: NsdServiceInfo) {
            Log.d(TAG, "mDNS service lost: ${serviceInfo.serviceName}")
        }

        override fun onDiscoveryStopped(serviceType: String) {
            Log.d(TAG, "mDNS discovery stopped for $serviceType")
        }

        override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
            Log.e(TAG, "mDNS discovery start failed: type=$serviceType error=$errorCode")
            discovering = false
        }

        override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
            Log.e(TAG, "mDNS discovery stop failed: type=$serviceType error=$errorCode")
        }
    }

    fun startDiscovery() {
        if (discovering) return
        discovering = true
        try {
            nsdManager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, discoveryListener)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start mDNS discovery", e)
            discovering = false
        }
    }

    fun stopDiscovery() {
        if (!discovering) return
        discovering = false
        try {
            nsdManager.stopServiceDiscovery(discoveryListener)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to stop mDNS discovery", e)
        }
    }

    fun clearPort() {
        discoveredPort.set(0)
    }

    companion object {
        private const val TAG = "AdbPortDiscovery"
        private const val SERVICE_TYPE = "_adb-tls-connect._tcp."

        private fun isLocalAddress(addr: java.net.InetAddress): Boolean {
            if (addr.isLoopbackAddress) return true
            val hostAddr = addr.hostAddress ?: return false
            return try {
                NetworkInterface.getNetworkInterfaces()?.asSequence()
                    ?.flatMap { it.inetAddresses.asSequence() }
                    ?.any { it.hostAddress == hostAddr }
                    ?: false
            } catch (e: Exception) {
                Log.e(TAG, "Failed to enumerate network interfaces", e)
                false
            }
        }
    }
}
