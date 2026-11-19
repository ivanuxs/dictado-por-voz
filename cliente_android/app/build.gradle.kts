import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Credenciales del puente que se pueden meter DENTRO del APK. Se leen de
// local.properties, que NO se versiona (así el token y la clave no acaban en el
// repositorio público):
//
//     dictadoUrl=https://dictado.tu-dominio.com
//     dictadoToken=...
//     dictadoClave=...
//
// OJO, para el APK de Google Play: compilar SIN dictadoToken ni dictadoClave.
// El APK público no debe llevar secretos; cada usuario entra con su cuenta
// («Cuenta y plan») y recibe su propio token. Si las líneas están puestas, el
// token maestro viaja dentro del APK y cualquiera que lo descompile lo tiene.
val credenciales = Properties().apply {
    val archivo = rootProject.file("local.properties")
    if (archivo.exists()) archivo.inputStream().use { load(it) }
}

val credencial: (String) -> String = { nombre ->
    (credenciales.getProperty(nombre) ?: "").trim()
        .replace("\\", "\\\\")
        .replace("\"", "\\\"")
}

// APK para OTRO usuario: se compila con -PsinToken y el token maestro se queda
// fuera (la dirección y la clave del proxy sí van dentro). Si el token viajara,
// la app arrancaría con la sesión maestra —sin plan ni cupos— y la pantalla
// «Cuenta» no ofrecería crear la cuenta, porque ya se creería dentro.
val sinToken: Boolean = providers.gradleProperty("sinToken").orNull
    ?.lowercase()
    ?.let { it !in setOf("false", "0", "no") }
    ?: false

android {
    namespace = "com.ivanuxs.skipkey"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.ivanuxs.skipkey"
        minSdk = 26
        targetSdk = 34
        versionCode = 3
        versionName = "1.2"

        val urlBase = credencial("dictadoUrl")
        val token = if (sinToken) "" else credencial("dictadoToken")
        val clave = credencial("dictadoClave")
        buildConfigField("String", "URL_BASE", "\"$urlBase\"")
        buildConfigField("String", "TOKEN", "\"$token\"")
        buildConfigField("String", "CLAVE", "\"$clave\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    // Necesario en AGP 8 para que se genere la clase BuildConfig.
    buildFeatures {
        buildConfig = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    // Red: OkHttp trae el cliente HTTP y el WebSocket en la misma librería.
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // Token y clave guardados cifrados en el dispositivo.
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
}
