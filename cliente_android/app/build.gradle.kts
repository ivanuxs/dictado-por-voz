import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Credenciales del puente que se meten DENTRO del APK, para no tener que
// escribirlas en el celular. Se leen de local.properties, que NO se versiona
// (así el token y la clave no acaban en el repositorio público):
//
//     dictadoUrl=https://dictado.tu-dominio.com
//     dictadoToken=...
//     dictadoClave=...
//
// Si faltan, la app arranca sin configurar y se rellenan en su pantalla.
val credenciales = Properties().apply {
    val archivo = rootProject.file("local.properties")
    if (archivo.exists()) archivo.inputStream().use { load(it) }
}

val credencial: (String) -> String = { nombre ->
    (credenciales.getProperty(nombre) ?: "").trim()
        .replace("\\", "\\\\")
        .replace("\"", "\\\"")
}

android {
    namespace = "com.ivanuxs.dictadoime"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.ivanuxs.dictadoime"
        minSdk = 26
        targetSdk = 34
        versionCode = 2
        versionName = "1.1"

        val urlBase = credencial("dictadoUrl")
        val token = credencial("dictadoToken")
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
