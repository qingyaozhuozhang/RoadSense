$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$wrapper = Join-Path $PSScriptRoot 'gradle\wrapper\gradle-wrapper.jar'
if (!(Test-Path $wrapper)) {
    Write-Host 'Fetching the official Gradle wrapper jar for Gradle 8.13...'
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/gradle/gradle/v8.13.0/gradle/wrapper/gradle-wrapper.jar' -OutFile $wrapper
}
Write-Host 'Done. Open this folder in Android Studio. The IDE will download the Gradle distribution and SDK packages.'
