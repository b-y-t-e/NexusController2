import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

/*
 * The release signing key, from a local file or from the environment.
 *
 * Android refuses to update an installed app when the new APK carries a
 * different signature, and a debug build is signed with ~/.android/debug.keystore
 * — a file the CI runner *generates on the spot*, differently on every run. Every
 * release built that way was therefore an uninstall-and-lose-your-layouts, which
 * is why in-app updating could not exist. One stable key fixes that, and the key
 * has to outlive the machine: keep a backup, because losing it means every
 * installed copy is stranded on the version it has.
 *
 * Two sources, same four values. `android/keystore.properties` is this machine
 * (gitignored, and it only points at a key kept outside the tree); the
 * environment is CI, where the key arrives as a repository secret. Neither being
 * present is not an error — a contributor without the key still builds and runs
 * debug — but then `release` is left unsigned and every path that ships one says
 * so out loud rather than producing an APK no phone will install.
 */
val keystoreProperties = Properties().apply {
    val file = rootProject.file("keystore.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}

fun signingValue(property: String, environment: String): String? =
    (keystoreProperties.getProperty(property) ?: System.getenv(environment))?.takeIf { it.isNotBlank() }

/**
 * Why `release` is not going to be signed, or null when it is.
 *
 * Three situations, and they used to collapse into one: no key configured at
 * all, a key configured whose file is not there, and a key whose file is there
 * with a password missing. All three ended as "unsigned APK", and every one of
 * them was answered with "point keystore.properties at the key" — advice that is
 * only right for the first, and actively misleading for the other two, where the
 * file has been pointed at and the mistake is somewhere else entirely.
 */
val signingProblem: String? = run {
    val configured = signingValue("storeFile", "NEXUS_KEYSTORE")
        ?: return@run "no signing key is configured (android/keystore.properties, or NEXUS_KEYSTORE)"
    val store = file(configured)
    if (!store.isFile) {
        return@run "the configured signing key is not there: $configured"
    }
    val missing = listOf(
        "storePassword" to signingValue("storePassword", "NEXUS_KEYSTORE_PASSWORD"),
        "keyAlias" to signingValue("keyAlias", "NEXUS_KEY_ALIAS"),
        "keyPassword" to signingValue("keyPassword", "NEXUS_KEY_PASSWORD"),
    ).filter { (_, value) -> value == null }.map { (name, _) -> name }
    if (missing.isNotEmpty()) {
        return@run "the signing key at $configured is missing ${missing.joinToString(", ")}"
    }
    null
}

val signingStore: File? =
    if (signingProblem == null) file(signingValue("storeFile", "NEXUS_KEYSTORE")!!) else null

// Said once, at configuration time, so an interactive build shows the reason
// too — not only the release script.
if (signingProblem != null) {
    logger.lifecycle("Nexus: release builds will be UNSIGNED — $signingProblem")
}

android {
    namespace = "com.nexuscontroller.pad"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.nexuscontroller.pad"
        minSdk = 28
        targetSdk = 34
        versionCode = 2
        versionName = "2.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        vectorDrawables {
            useSupportLibrary = true
        }
    }

    /*
     * Two APKs from one source tree.
     *
     * `modern` is the one to install: API 28 upwards, which is what almost every
     * phone in use runs. `legacy` reaches back to Android 5 for the drawer full
     * of old phones that is exactly where a room of four Buzz buzzers comes from.
     * Nothing in the code differs: every call newer than API 21 has to be behind a
     * SDK_INT check, and `lintLegacyDebug` is what actually enforces that — it was
     * not true until lint was asked, and the app died on Android 5 to 7.
     */
    flavorDimensions += "api"
    productFlavors {
        create("modern") {
            dimension = "api"
            minSdk = 28
        }
        create("legacy") {
            dimension = "api"
            minSdk = 21
            versionNameSuffix = "-legacy"
        }
    }

    signingConfigs {
        if (signingStore != null) {
            create("release") {
                storeFile = signingStore
                storePassword = signingValue("storePassword", "NEXUS_KEYSTORE_PASSWORD")
                keyAlias = signingValue("keyAlias", "NEXUS_KEY_ALIAS")
                keyPassword = signingValue("keyPassword", "NEXUS_KEY_PASSWORD")
                // v1 as well as v2 and v3: the legacy flavour installs back to
                // Android 5, which predates the v2 scheme entirely and would
                // reject a v2-only APK.
                //
                // v3 is not decoration. It is the scheme that carries a rotation
                // proof, and rotation is the only way out of the situation this
                // whole signing setup warns about — a lost key strands every
                // installed copy. A phone can only accept a rotated key if the
                // copy it already has was signed with v3, so leaving it off (the
                // AGP default) would mean every release shipped until then can
                // never be rotated away from.
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        // The app has to know its own version to ask whether a newer one exists,
        // and its own flavour to download the APK that will actually install on
        // this phone. Both come from here rather than from a literal somebody has
        // to remember to bump — see UpdateCheck.
        buildConfig = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.15"
    }
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    /*
     * Lint gates the release (`build_release.py` and the tag workflow), so what it
     * may fail a build for has to be a decision rather than whatever the defaults
     * happen to be on the day.
     *
     * What it is here for: calls to APIs newer than a flavour's minSdk. Those
     * compile, pass every unit test and work on the phone in your hand, then throw
     * NoClassDefFoundError on an older one — and that is an Error, so no
     * `catch (e: Exception)` sees it. That is the check worth stopping a release.
     *
     * What must never stop one: verdicts that depend on the calendar or on a
     * network lookup rather than on this source tree. Left at their defaults, a
     * library publishing a new version overnight, or a Play deadline passing,
     * would make a tag unpublishable without a line of ours having changed. They
     * stay visible as warnings — they are worth reading — but they do not vote.
     *
     * Deliberately no baseline: the report is clean, and a baseline is a list of
     * accepted failures that only ever grows.
     */
    lint {
        abortOnError = true
        warningsAsErrors = false
        checkDependencies = false
        informational += setOf("GradleDependency")
        warning += setOf("ExpiredTargetSdkVersion", "OldTargetApi")
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.4")
    implementation("androidx.activity:activity-compose:1.9.1")
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
