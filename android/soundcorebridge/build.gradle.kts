plugins {
    id("com.android.library")
    kotlin("android")
}
android {
    namespace = "com.sonic.hardware"
    compileSdk = 35
    defaultConfig { minSdk = 26 }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}
dependencies {
    api("com.oceanwing.soundcore:spplink:1.3.02")
    // The shipped V1.3.02 AAR calls io.ktor.client.plugins.* (Ktor 2.x).
    // The bundle's Android dependency table still says 1.6.8; that ABI crashes at init.
    // V1.3.02 calls Job.cancel$default on the interface (new JVM default ABI).
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.5.0")
    implementation("com.squareup.okhttp3:okhttp:3.12.13")
    implementation("com.squareup.okio:okio:1.17.4")
    implementation("com.ditchoom:buffer:1.2.1")
    implementation("org.jetbrains.kotlinx:atomicfu:0.20.2")
    implementation("androidx.annotation:annotation-jvm:1.6.0")
    implementation("com.github.getActivity:XXPermissions:20.0")
    implementation("com.madgag.spongycastle:core:1.58.0.0")
    implementation("com.madgag.spongycastle:prov:1.58.0.0")
    implementation("com.google.code.gson:gson:2.10")
    implementation("io.ktor:ktor-client-core:2.3.13")
    implementation("io.ktor:ktor-client-okhttp:2.3.13")
    implementation("io.ktor:ktor-client-websockets:2.3.13")
    implementation("io.ktor:ktor-client-logging:2.3.13")
    testImplementation("junit:junit:4.13.2")
}
tasks.register("writeTestClasspath") {
    doLast {
        val test = tasks.named<Test>("testDebugUnitTest").get()
        layout.buildDirectory.file("test-classpath.txt").get().asFile.writeText(test.classpath.asPath)
    }
}
