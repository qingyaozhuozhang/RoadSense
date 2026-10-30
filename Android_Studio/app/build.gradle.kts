import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
// Built-in values match the supplied RoadSense cloud. Local overrides are optional.
val roadSenseLocal = Properties().apply {
    val config = rootProject.file("local.properties")
    if (config.isFile) config.inputStream().use { load(it) }
}
val cloudUrl = roadSenseLocal.getProperty("roadsense.cloudBaseUrl", "http://39.102.84.218:5000")
val androidMapKey = roadSenseLocal.getProperty("roadsense.amapAndroidKey", "810389ff3d0f0ebfde4f774621be874b")

android {
    namespace = "com.roadsense.inspection"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.roadsense.inspection"
        minSdk = 26
        targetSdk = 35
        versionCode = 10
        versionName = "1.5.2"
        // Existing RoadSense Flask cloud endpoint (HTTP until TLS is configured).
        buildConfigField("String", "CLOUD_BASE_URL", "\"$cloudUrl\"")
        buildConfigField("String", "AMAP_ANDROID_KEY", "\"$androidMapKey\"")
        manifestPlaceholders["AMAP_ANDROID_KEY"] = androidMapKey
    }
    buildFeatures { buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    
    buildTypes {
        release { isMinifyEnabled = false }
        debug { isMinifyEnabled = false }
    }
}
kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-ktx:1.10.1")
    // Official AMap Android Location SDK (Web API key is NOT used here).
    implementation("com.amap.api:location:6.5.1")
}
