package com.nicobrailo.nannygodmin

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.graphics.Bitmap
import android.os.Build
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import java.util.concurrent.Executor

class NannyAccessibilityService : AccessibilityService() {

    companion object {
        private var instance: NannyAccessibilityService? = null
        private const val TAG = "NannyAccessibility"

        fun takeScreenshot(executor: Executor, callback: (Bitmap?) -> Unit) {
            val service = instance
            if (service == null) {
                Log.e(TAG, "Accessibility Service not running")
                callback(null)
                return
            }

            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
                Log.e(TAG, "Screenshot not supported on this API level via AccessibilityService")
                callback(null)
                return;
            }

            service.takeScreenshot(android.view.Display.DEFAULT_DISPLAY, executor, object : TakeScreenshotCallback {
                override fun onSuccess(screenshot: ScreenshotResult) {
                    val hardwareBuffer = screenshot.hardwareBuffer
                    val bitmap = Bitmap.wrapHardwareBuffer(hardwareBuffer, screenshot.colorSpace)
                    // Note: Bitmap.wrapHardwareBuffer returns an immutable hardware bitmap.
                    // We might need to copy it if we want to compress it.
                    val softwareBitmap = bitmap?.copy(Bitmap.Config.ARGB_8888, false)
                    callback(softwareBitmap)
                    hardwareBuffer.close()
                }

                override fun onFailure(errorCode: Int) {
                    Log.e(TAG, "Screenshot failed: $errorCode")
                    callback(null)
                }
            })
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        Log.i(TAG, "Accessibility Service Connected")
        instance = this
        val info = AccessibilityServiceInfo().apply {
            eventTypes = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED or AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
            feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
            notificationTimeout = 100
        }
        this.serviceInfo = info
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        instance = null
        return super.onUnbind(intent)
    }
}
