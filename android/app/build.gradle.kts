plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
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

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
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
